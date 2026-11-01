"""The coordinator: admit, start, wait, gate, retry with the gate's feedback, release.

Each lane gets its own git worktree and branch, so parallel agents never share a working tree.
State lives in the repository's git directory (`.git/laneproof/`), so it is never committed and
every worktree of the repository sees the same registry.
"""
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field

from . import admission
from .agents import stop
from .gate import RECEIPT, verify
from .git import git

LANE_RULES = """\
## How this lane works

- You are in your own git worktree. Other agents work in parallel in their own worktrees.
  Stay inside this directory.
- You do not need to commit; the coordinator commits whatever you leave here.
- When you are finished, write `{receipt}` at the root of this worktree:

  {{"status": "done",
   "summary": "one or two lines",
   "claims": [
     {{"criterion": "c1", "label": "FACT", "cmd": "python3 -m unittest tests.test_x", "expect_exit": 0}},
     {{"criterion": "c2", "label": "FACT", "cmd": "python3 -m mypkg.cli foo bar", "stdout_contains": "baz"}},
     {{"label": "CLAIM", "text": "something you believe but did not check"}}
   ]}}

- Label every claim FACT (a command that proves it), CLAIM (unchecked) or HYPOTHESIS.
  Only FACTs count, and every criterion above needs at least one FACT.
- The gate re-runs every FACT command, from the repository root, on your branch merged with the
  latest {target}. Other lanes may have landed since you started, so a check that only passes on
  your branch alone will fail there. Your work lands only if every FACT and the coordinator's own
  check pass.
- If you cannot finish without an answer from a human, write status "asking" and a "question".
"""


def state_dir(repo):
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    d = os.path.join(common, "laneproof")
    os.makedirs(d, exist_ok=True)
    exclude = os.path.join(common, "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    have = open(exclude).read() if os.path.exists(exclude) else ""
    if RECEIPT not in have:
        with open(exclude, "a") as f:
            f.write(f"\n{RECEIPT}\n__pycache__/\n")
    return d


def worktree_root(repo):
    top = git(repo, "rev-parse", "--show-toplevel")
    tag = hashlib.sha256(top.encode()).hexdigest()[:6]
    return os.path.join(os.path.expanduser(os.environ.get("LANEPROOF_HOME", "~/.laneproof")),
                        "worktrees", f"{os.path.basename(top)}-{tag}")


def lane_prompt(task, target):
    crit = "\n".join(f"- [{c.id}] {c.text}" for c in task.criteria)
    return f"{task.prompt}\n\n## Acceptance criteria\n\n{crit}\n\n" + LANE_RULES.format(receipt=RECEIPT, target=target)


@dataclass
class LaneResult:
    lane: str
    task: str
    state: str                     # landed | asking | failed | timeout
    attempts: int
    reservation: str
    seconds: float
    commit: str | None = None
    verdicts: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)


class Coordinator:
    def __init__(self, repo, agent, limits, target="main", retries=2, lane_timeout=1800, poll=2.0, say=print, union=True):
        self.repo = git(repo, "rev-parse", "--show-toplevel")
        self.agent, self.limits, self.target = agent, limits, target
        self.retries, self.lane_timeout, self.poll, self.say, self.union = retries, lane_timeout, poll, say, union
        self.registry = admission.Registry(state_dir(self.repo))
        self.wt_root = worktree_root(self.repo)

    def run(self, tasks):
        pending, active, done, waiting = list(tasks), {}, [], set()
        while pending or active:
            while pending:
                try:
                    res = admission.reserve(self.registry, pending[0].id, self.limits, self.repo)
                except admission.NoHeadroom as e:
                    if pending[0].id not in waiting:
                        waiting.add(pending[0].id)
                        self.say(f"queued  {pending[0].id:<28} {e}")
                    break
                task = pending.pop(0)
                active[task.id] = self._start(task, res)
            for lid in list(active):
                lane = active[lid]
                if lane["proc"].poll() is None:
                    if time.time() - lane["t0"] > self.lane_timeout:
                        stop(lane["proc"])
                        done.append(self._finish(lane, "timeout"))
                        del active[lid]
                        continue
                    if lane["turns"] is None or self.agent.turns_finished(lane["log"]) <= lane["turns"]:
                        continue
                    stop(lane["proc"])  # the run reported its end; a background job it left must not hold the lane
                if self._judge(lane):
                    done.append(self._finish(lane, lane["outcome"]))
                    del active[lid]
            time.sleep(self.poll)
        return done

    def _start(self, task, res):
        admission.claim(self.registry, res["id"], res["token"])
        lane = f"{task.id}-{res['id']}"
        wt = os.path.join(self.wt_root, lane)
        os.makedirs(self.wt_root, exist_ok=True)
        git(self.repo, "worktree", "add", "-q", "-b", f"laneproof/{lane}", wt, self.target)
        log = os.path.join(self.registry.dir, "logs", lane + ".jsonl")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        turns = self.agent.turns_finished(log)
        proc = self.agent.launch(wt, lane_prompt(task, self.target), log)
        self._record(lane, pid=proc.pid, reservation=res["id"], worktree=wt, task=task.id, state="working")
        self.registry.log(op="start", lane=lane, reservation=res["id"], pid=proc.pid)
        self.say(f"start   {lane:<28} reservation {res['id']} (pid {proc.pid})")
        return {"lane": lane, "task": task, "res": res, "wt": wt, "log": log, "proc": proc, "turns": turns,
                "attempts": 1, "t0": time.time(), "verdicts": []}

    def _judge(self, lane):
        """True when the lane is finished, one way or another; False when it was sent back to work."""
        v = verify(self.repo, self.registry, lane["lane"], lane["wt"], lane["task"], self.target, union=self.union)
        lane["verdicts"].append({"state": v.state, "reasons": v.reasons, "commit": v.commit,
                                 "replays": [{k: r[k] for k in ("cmd", "ok", "exit")} for r in v.replays]})
        if v.landed:
            self.say(f"landed  {lane['lane']:<28} {v.commit[:10]} ({len(v.replays)} checks re-run on merged {self.target})")
            lane.update(outcome="landed", commit=v.commit)
            return True
        if v.state == "asking":
            self.say(f"asking  {lane['lane']:<28} {v.question}")
            lane["outcome"] = "asking"
            return True
        self.say(f"reject  {lane['lane']:<28} " + " | ".join(r.splitlines()[0][:110] for r in v.reasons))
        if lane["attempts"] > self.retries:
            lane["outcome"] = "failed"
            return True
        lane["attempts"] += 1
        sid = self.agent.usage(lane["log"]).get("session_id") if self.agent.resumable else None
        prompt = v.feedback() if sid else lane_prompt(lane["task"], self.target) + "\n\n" + v.feedback()
        lane["turns"] = self.agent.turns_finished(lane["log"])
        lane["proc"] = self.agent.launch(lane["wt"], prompt, lane["log"], resume=sid)
        self._record(lane["lane"], pid=lane["proc"].pid, state="working", attempts=lane["attempts"])
        self.say(f"retry   {lane['lane']:<28} attempt {lane['attempts']} with the gate's feedback")
        return False

    def _finish(self, lane, outcome):
        admission.release(self.registry, lane["res"]["id"], outcome)
        self._record(lane["lane"], state=outcome, pid=None)
        return LaneResult(lane=lane["lane"], task=lane["task"].id, state=outcome, attempts=lane["attempts"],
                          reservation=lane["res"]["id"], seconds=round(time.time() - lane["t0"], 1),
                          commit=lane.get("commit"), verdicts=lane["verdicts"], usage=self.agent.usage(lane["log"]))

    def _record(self, lane, **kw):
        with self.registry.locked() as reg:
            reg["lanes"].setdefault(lane, {}).update(kw, updated=time.time())
            self.registry.save(reg)


def save_results(registry, results):
    path = os.path.join(registry.dir, "runs", time.strftime("%Y%m%d-%H%M%S") + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=1)
    return path
