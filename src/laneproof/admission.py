"""Admission: may another agent start right now?

One registry per repository, every write under one flock. A start needs a reservation, and a
reservation is claimed exactly once: the claim creates a start marker with O_EXCL before any agent
process exists, so a retried, replayed or duplicated start can never launch a second agent on the
same reservation. Occupancy is observed, not trusted: a lane process that is still alive counts
against the cap even when its reservation was released. Anything unreadable refuses.
"""
import fcntl
import json
import os
import secrets
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass

OK, REFUSED, NO_HEADROOM, UNVERIFIABLE = 0, 1, 2, 3
HELD = ("granted", "claimed")


class Refused(Exception):
    code = REFUSED


class NoHeadroom(Refused):
    code = NO_HEADROOM


class Unverifiable(Refused):
    code = UNVERIFIABLE


@dataclass
class Limits:
    cap: int = 4                     # concurrent agents
    min_free_gb: float = 1.0         # free disk on the repository's volume
    max_load_per_cpu: float | None = None  # 1-minute load average divided by CPU count
    quota_cmd: str | None = None     # exit 0 means the account has quota for one more agent


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Registry:
    def __init__(self, state_dir):
        self.dir = state_dir
        self.path = os.path.join(state_dir, "registry.json")
        self.events = os.path.join(state_dir, "events.jsonl")
        self.starts = os.path.join(state_dir, "starts")
        os.makedirs(self.starts, exist_ok=True)

    @contextmanager
    def locked(self):
        with open(os.path.join(self.dir, "registry.lock"), "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield self._load()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def read(self):
        return self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return {"reservations": [], "lanes": {}, "landed": {}}
        try:
            with open(self.path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise Unverifiable(f"registry unreadable: {e}")

    def save(self, reg):
        tmp = self.path + f".tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(reg, f, indent=1)
        os.replace(tmp, self.path)

    def log(self, **ev):
        ev["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(self.events, "a") as f:
            f.write(json.dumps(ev) + "\n")


def occupancy(reg):
    """Held reservations plus live lane processes that no held reservation covers."""
    held = [r for r in reg["reservations"] if r["state"] in HELD]
    held_ids = {r["id"] for r in held}
    orphans = [name for name, lane in reg["lanes"].items()
               if pid_alive(lane.get("pid")) and lane.get("reservation") not in held_ids]
    return held, orphans


def headroom(limits, where):
    """Every reason a new agent would not fit now; [] means it fits."""
    why = []
    try:
        free = shutil.disk_usage(where).free / 1e9
    except OSError as e:
        raise Unverifiable(f"disk unreadable: {e}")
    if free < limits.min_free_gb:
        why.append(f"disk {free:.1f} GB free < {limits.min_free_gb} GB")
    if limits.max_load_per_cpu is not None:
        try:
            load = os.getloadavg()[0] / (os.cpu_count() or 1)
        except OSError as e:
            raise Unverifiable(f"load unreadable: {e}")
        if load > limits.max_load_per_cpu:
            why.append(f"load {load:.2f}/cpu > {limits.max_load_per_cpu}")
    if limits.quota_cmd:
        try:
            q = subprocess.run(limits.quota_cmd, shell=True, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise Unverifiable("quota command timed out")
        if q.returncode != 0:
            why.append("quota: " + (q.stdout.strip() or q.stderr.strip() or f"exit {q.returncode}"))
    return why


def reserve(registry, holder, limits, where):
    """Grant one reservation if the cap and the machine allow it."""
    with registry.locked() as reg:
        held, orphans = occupancy(reg)
        used = len(held) + len(orphans)
        if used >= limits.cap:
            registry.log(op="reserve-refused", holder=holder, used=used, cap=limits.cap)
            raise NoHeadroom(f"cap {limits.cap} full ({len(held)} reserved, {len(orphans)} unreserved live)")
        why = headroom(limits, where)
        if why:
            registry.log(op="reserve-refused", holder=holder, why=why)
            raise NoHeadroom("no headroom: " + "; ".join(why))
        res = {"id": f"R{len(reg['reservations']) + 1:03d}", "holder": holder, "state": "granted",
               "token": secrets.token_hex(4), "granted_at": time.time()}
        reg["reservations"].append(res)
        registry.save(reg)
        registry.log(op="reserve", id=res["id"], holder=holder, used=used + 1, cap=limits.cap)
        return res


def claim(registry, rid, token):
    """Turn a granted reservation into the right to start exactly one agent."""
    with registry.locked() as reg:
        res = next((r for r in reg["reservations"] if r["id"] == rid), None)
        if not res or res["token"] != token:
            raise Refused(f"no reservation {rid} with that token")
        try:
            fd = os.open(os.path.join(registry.starts, rid), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            registry.log(op="claim-refused", id=rid, why="already started")
            raise Refused(f"{rid} already started an agent; one reservation, one start")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        if res["state"] != "granted":
            raise Refused(f"{rid} is {res['state']}, not granted")
        res.update(state="claimed", claimed_at=time.time())
        registry.save(reg)
        registry.log(op="claim", id=rid)
        return res


def release(registry, rid, reason):
    with registry.locked() as reg:
        res = next((r for r in reg["reservations"] if r["id"] == rid and r["state"] in HELD), None)
        if not res:
            raise Refused(f"no held reservation {rid}")
        res.update(state="released", released_at=time.time(), reason=reason)
        registry.save(reg)
        registry.log(op="release", id=rid, reason=reason)
