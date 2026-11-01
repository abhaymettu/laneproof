"""The evidence gate: a lane's "done" counts only after its evidence re-runs on the merged result.

A lane ends by writing a receipt. Every claim in it carries a label: FACT (a command anyone can
re-run, with the result it should give), CLAIM (someone said so) or HYPOTHESIS. Only FACTs count,
and every acceptance criterion needs at least one. The gate then, one lane at a time:

  1. checks the receipt covers every criterion with a FACT;
  2. checks the diff stays inside the task's scope and leaves protected files alone;
  3. merges the lane onto the current target branch in a scratch worktree;
  4. re-runs every FACT command and the coordinator's own check on that merged tree;
  5. only if all of that passes, moves the target branch to the merge (compare-and-swap).

Landing is idempotent: the same receipt on the same commit lands once, and a replay is a no-op.
"""
import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field

from .git import GitError, changed_files, commit_all, git

RECEIPT = ".laneproof-receipt.json"
LABELS = ("FACT", "CLAIM", "HYPOTHESIS")
TAIL = 1200


@dataclass
class Verdict:
    state: str                      # landed | rejected | asking | already
    reasons: list = field(default_factory=list)
    replays: list = field(default_factory=list)
    commit: str | None = None
    question: str | None = None

    @property
    def landed(self):
        return self.state in ("landed", "already")

    def feedback(self):
        lines = ["The laneproof gate did not accept your receipt. Nothing you did has landed yet.", ""]
        lines += [f"- {r}" for r in self.reasons]
        lines += ["", "Fix each point in your worktree, re-run your checks, rewrite the receipt with status done, then stop."]
        return "\n".join(lines)


def read_receipt(worktree):
    path = os.path.join(worktree, RECEIPT)
    try:
        with open(path, "rb") as f:
            raw = f.read()
        return json.loads(raw), raw
    except FileNotFoundError:
        return None, b""
    except (OSError, json.JSONDecodeError) as e:
        return {"_error": str(e)}, b""


def receipt_problems(receipt, task):
    if receipt is None:
        return [f"no receipt: write {RECEIPT} at the root of your worktree"]
    if "_error" in receipt:
        return [f"receipt is not valid JSON: {receipt['_error']}"]
    probs, covered = [], set()
    for i, c in enumerate(receipt.get("claims", [])):
        label = c.get("label")
        if label not in LABELS:
            probs.append(f"claim {i + 1} has label {label!r}; use FACT, CLAIM or HYPOTHESIS")
        elif label == "FACT":
            if not c.get("cmd"):
                probs.append(f"claim {i + 1} is a FACT with no cmd to re-run")
            else:
                covered.update(str(x) for x in _as_list(c.get("criterion")))
    for crit in task.criteria:
        if crit.id not in covered:
            probs.append(f"criterion {crit.id} ({crit.text}) has no FACT claim with a command that proves it")
    return probs


def replay(cmd, cwd, expect_exit=0, stdout_contains=None, timeout=300):
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        code, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        code, out = None, f"timed out after {timeout}s"
    ok = code == expect_exit and (stdout_contains is None or stdout_contains in out)
    return {"cmd": cmd, "ok": ok, "exit": code, "expect_exit": expect_exit,
            "stdout_contains": stdout_contains, "tail": out[-TAIL:]}


def _as_list(x):
    return x if isinstance(x, list) else [x] if x is not None else []


def _receipt_key(raw, head):
    return hashlib.sha256(raw + head.encode()).hexdigest()[:16]


def verify(repo, registry, lane, worktree, task, target="main", timeout=300, union=True):
    """Judge one lane and land it if the evidence holds. Never raises for a bad lane; it returns a verdict.

    union: when the merge conflicts, try keeping both sides of every conflicting hunk. That is only safe
    because nothing lands until every FACT and the coordinator's check pass on the result; when they do not,
    the lane gets the conflict back as usual.
    """
    receipt, raw = read_receipt(worktree)
    if receipt and receipt.get("status") in ("asking", "blocked"):
        return Verdict("asking", question=receipt.get("question") or receipt.get("summary"))
    probs = receipt_problems(receipt, task)
    if receipt and receipt.get("status") != "done" and "_error" not in receipt:
        probs.insert(0, f"receipt status is {receipt.get('status')!r}, not 'done'")
    head = commit_all(worktree, f"{lane}: {task.title}")
    if probs:
        return Verdict("rejected", probs, commit=head)

    key = _receipt_key(raw, head)
    if key in registry.read().get("landed", {}):
        return Verdict("already", commit=registry.read()["landed"][key]["commit"])

    with open(os.path.join(registry.dir, "land.lock"), "a") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        old = git(repo, "rev-parse", f"refs/heads/{target}")
        base = git(repo, "merge-base", old, head)
        bad = task.out_of_scope(changed_files(repo, base, head))
        if bad:
            return Verdict("rejected", [f"changed files outside this task's scope: {', '.join(bad)}"], commit=head)
        scratch = tempfile.mkdtemp(prefix=f"laneproof-{lane}-")
        try:
            git(repo, "worktree", "add", "-q", "--detach", scratch, old)
            m = subprocess.run(["git", "-C", scratch, "-c", "user.name=laneproof", "-c", "user.email=laneproof@localhost",
                                "merge", "--no-ff", "--no-edit", "-m", f"land {lane}: {task.title}", head],
                               capture_output=True, text=True)
            conflict = []
            if m.returncode != 0:
                conflict = git(scratch, "diff", "--name-only", "--diff-filter=U", check=False).split()
                if not (union and _union_resolve(scratch, conflict)):
                    return Verdict("rejected", [_conflict_reason(conflict, target)], commit=head)
            replays = []
            for c in receipt["claims"]:
                if c.get("label") == "FACT":
                    replays.append(replay(c["cmd"], scratch, c.get("expect_exit", 0), c.get("stdout_contains"), timeout))
            if task.check:
                replays.append({**replay(task.check, scratch, timeout=timeout), "coordinator": True})
            failed = [r for r in replays if not r["ok"]]
            if failed:
                reasons = [("coordinator check" if r.get("coordinator") else "your FACT") +
                           f" `{r['cmd']}` gave exit {r['exit']} (expected {r['expect_exit']}"
                           + (f", output containing {r['stdout_contains']!r}" if r["stdout_contains"] else "")
                           + f") on your branch merged with {target}. Output tail:\n{r['tail']}" for r in failed]
                if conflict:
                    reasons.insert(0, _conflict_reason(conflict, target) + ". (A union merge was tried; the checks "
                                   "below failed on it, so resolve by hand.)")
                return Verdict("rejected", reasons, replays, commit=head)
            merged = git(scratch, "rev-parse", "HEAD")
            _advance(repo, target, old, merged)
        finally:
            git(repo, "worktree", "remove", "--force", scratch, check=False)
        with registry.locked() as reg:
            reg.setdefault("landed", {})[key] = {"lane": lane, "commit": merged, "task": task.id}
            registry.save(reg)
        registry.log(op="land", lane=lane, task=task.id, commit=merged, replays=len(replays))
        return Verdict("landed", replays=replays, commit=merged)


def _conflict_reason(files, target):
    return (f"your branch conflicts with {target} in: {', '.join(files) or 'unknown files'}. "
            f"Run `git merge {target}` in your worktree, resolve, keep both sides' intent, and re-run your checks")


def _union_resolve(scratch, files):
    """Resolve text conflicts by keeping both sides, then commit the merge. False if any file cannot be."""
    if not files:
        return False
    for f in files:
        stages = {}
        for n in (1, 2, 3):
            p = subprocess.run(["git", "-C", scratch, "show", f":{n}:{f}"], capture_output=True)
            stages[n] = p.stdout if p.returncode == 0 else None
        if stages[2] is None or stages[3] is None or b"\0" in stages[2] + stages[3]:
            return False  # delete/modify or binary: not a text merge
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for n in (2, 1, 3):
                path = os.path.join(d, str(n))
                with open(path, "wb") as fh:
                    fh.write(stages[n] or b"")
                paths.append(path)
            out = subprocess.run(["git", "merge-file", "-p", "--union", *paths], capture_output=True)
        with open(os.path.join(scratch, f), "wb") as fh:
            fh.write(out.stdout)
        git(scratch, "add", "--", f)
    git(scratch, "-c", "user.name=laneproof", "-c", "user.email=laneproof@localhost", "commit", "-q", "--no-edit")
    return True


def _advance(repo, target, old, new):
    """Move the target branch from old to new, and the main checkout with it when it has the branch out."""
    if git(repo, "symbolic-ref", "-q", "HEAD", check=False) == f"refs/heads/{target}":
        if git(repo, "status", "--porcelain", "--untracked-files=no"):
            raise GitError(f"{repo} has uncommitted changes on {target}; refusing to move it")
        git(repo, "merge", "-q", "--ff-only", new)
    else:
        git(repo, "update-ref", f"refs/heads/{target}", new, old)
