"""Run the benchmark: the same 8 tasks under four conditions, real `claude -p` agents.

  python3 bench/run.py --condition gated --parallel 4 --model sonnet --rep 1

  seq       one agent at a time in one checkout; each task committed when its agent exits
  shared    N agents at once in one checkout (no isolation, no gate)
  worktree  N agents, one git worktree each, each branch merged into main when its agent says done
  gated     laneproof: admission (cap N), one worktree each, receipts, evidence gate on the merged tree,
            up to 2 retries with the gate's feedback

Every condition gets the same task text and acceptance criteria. Only `gated` is told about receipts.
Each run writes bench/runs/<condition>-r<rep>/: per-lane agent logs (stream-json), events, results.json.
Score with bench/score.py.
"""
import argparse
import concurrent.futures
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from laneproof import admission  # noqa: E402
from laneproof.agents import ClaudeAgent  # noqa: E402
from laneproof.git import commit_all, git  # noqa: E402
from laneproof.lanes import Coordinator  # noqa: E402
from laneproof.tasks import load  # noqa: E402

# Keep the operator's own setup out of the runs: no user hooks, plugins, MCP servers, CLAUDE.md or memory.
ISOLATION = ["--setting-sources", "project", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
             "--settings", json.dumps({"claudeMdExcludes": [os.path.expanduser("~") + "/**"], "autoMemoryEnabled": False}),
             "--tools", "Bash,Read,Edit,Write"]

PLAIN_RULES = """\
## How to finish

{where}
- You do not need to commit.
- Check your own work before you finish.
- End your final message with exactly one line: `STATUS: done` or `STATUS: blocked: <reason>`.
"""
WHERE = {
    "seq": "- You are the only agent working in this repository right now.",
    "shared": "- Other agents are working on other tasks in this same directory at the same time.",
    "worktree": "- You are in your own git worktree. Other agents work on other tasks in parallel in their own\n"
                "  worktrees; your branch is merged into main when you say you are done.",
}


def plain_prompt(task, cond):
    crit = "\n".join(f"- [{c.id}] {c.text}" for c in task.criteria)
    return f"{task.prompt}\n\n## Acceptance criteria\n\n{crit}\n\n" + PLAIN_RULES.format(where=WHERE[cond])


def make_repo(root):
    repo = os.path.join(root, "textkit")
    shutil.copytree(os.path.join(HERE, "fixture"), repo)
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=bench", "-c", "user.email=bench@localhost", "commit", "-q", "-m", "textkit fixture"]):
        git(repo, *cmd)
    return repo


def snapshot(repo, message):
    """Commit the working tree as it is right now without touching the real index or HEAD."""
    env = {**os.environ, "GIT_INDEX_FILE": os.path.join(repo, ".git", f"snap-{os.getpid()}-{time.time_ns()}")}
    run = lambda *a: subprocess.run(["git", "-C", repo, *a], env=env, capture_output=True, text=True, check=True).stdout.strip()
    run("add", "-A")
    tree = run("write-tree")
    os.remove(env["GIT_INDEX_FILE"])
    head = git(repo, "rev-parse", "HEAD")
    commit = run("-c", "user.name=bench", "-c", "user.email=bench@localhost", "commit-tree", tree, "-p", head, "-m", message)
    git(repo, "update-ref", f"refs/snapshots/{message.split()[0]}", commit)  # so the bundle keeps it
    return commit


def status_line(text):
    lines = [l.strip().strip("`*") for l in (text or "").strip().splitlines() if l.strip()]
    last = lines[-1] if lines else ""
    return "done" if last.lower().startswith("status: done") else ("blocked" if "status: blocked" in last.lower() else "none")


def run_one(agent, workdir, prompt, log):
    t0 = time.time()
    agent.wait(agent.launch(workdir, prompt, log), log)
    u = agent.usage(log)
    return {"seconds": round(time.time() - t0, 1), "claim": status_line(u.get("final")), "usage": u}


def cond_seq(repo, tasks, agent, out, n):
    lanes = []
    for t in tasks:
        r = run_one(agent, repo, plain_prompt(t, "seq"), os.path.join(out, f"{t.id}.jsonl"))
        r.update(task=t.id, claim_commit=commit_all(repo, f"{t.id}"))
        lanes.append(r)
        print(f"  {t.id:<9} {r['claim']:<8} {r['seconds']}s", flush=True)
    return lanes


def cond_shared(repo, tasks, agent, out, n):
    def one(t):
        r = run_one(agent, repo, plain_prompt(t, "shared"), os.path.join(out, f"{t.id}.jsonl"))
        r.update(task=t.id, claim_commit=snapshot(repo, f"{t.id} at claim"))
        print(f"  {t.id:<9} {r['claim']:<8} {r['seconds']}s", flush=True)
        return r
    with concurrent.futures.ThreadPoolExecutor(n) as ex:
        lanes = list(ex.map(one, tasks))
    commit_all(repo, "all lanes")
    return lanes


def cond_worktree(repo, tasks, agent, out, n):
    wt_root = os.path.join(os.path.dirname(repo), "worktrees")

    def one(t):
        t0 = time.time()
        wt = os.path.join(wt_root, t.id)
        with merge_lock:  # concurrent `git worktree add` calls race on git's worktree metadata
            git(repo, "worktree", "add", "-q", "-b", f"lane/{t.id}", wt, "main")
        r = run_one(agent, wt, plain_prompt(t, "worktree"), os.path.join(out, f"{t.id}.jsonl"))
        r.update(task=t.id, claim_commit=commit_all(wt, t.id), merged=False, conflicts=[])
        log = os.path.join(out, f"{t.id}.jsonl")
        # Same conflict help the gated lanes get (2 retries), but nothing is re-checked before merging.
        for attempt in range(3):
            if r["claim"] != "done":
                break
            with merge_lock:
                m = subprocess.run(["git", "-C", repo, "-c", "user.name=bench", "-c", "user.email=bench@localhost",
                                    "merge", "--no-ff", "--no-edit", f"lane/{t.id}"], capture_output=True, text=True)
                if m.returncode == 0:
                    r["merged"] = True
                    break
                files = git(repo, "diff", "--name-only", "--diff-filter=U", check=False).split()
                git(repo, "merge", "--abort", check=False)
            r["conflicts"].append(files)
            if attempt == 2:
                break
            sid = agent.usage(log).get("session_id")
            fix = (f"Your branch conflicts with main in: {', '.join(files)}. Run `git merge main` in your worktree, "
                   "resolve the conflicts keeping both sides' intent, check your work, and end with the STATUS line.")
            agent.wait(agent.launch(wt, fix, log, resume=sid), log)
            r["claim"] = status_line(agent.usage(log).get("final"))
            r["claim_commit"] = commit_all(wt, f"{t.id} after conflict fix")
        r["usage"] = agent.usage(log)
        r["seconds"] = round(time.time() - t0, 1)
        print(f"  {t.id:<9} {r['claim']:<8} {r['seconds']}s merged={r['merged']} conflicts={len(r['conflicts'])}", flush=True)
        return r
    import threading
    merge_lock = threading.Lock()
    with concurrent.futures.ThreadPoolExecutor(n) as ex:
        return list(ex.map(one, tasks))


def cond_gated(repo, tasks, agent, out, n):
    os.environ["LANEPROOF_HOME"] = os.path.join(os.path.dirname(repo), "laneproof-home")
    coord = Coordinator(repo, agent, admission.Limits(cap=n, min_free_gb=0.5), retries=2, poll=3,
                        say=lambda s: print("  " + s, flush=True), union=os.environ.get("BENCH_GATE") != "strict")
    results = coord.run(tasks)
    for f in glob.glob(os.path.join(coord.registry.dir, "logs", "*.jsonl")):
        shutil.copy(f, os.path.join(out, os.path.basename(f).rsplit("-R", 1)[0] + ".jsonl"))
    shutil.copy(coord.registry.events, os.path.join(out, "events.jsonl"))
    return [{"task": r.task, "claim": "done" if r.state == "landed" else r.state, "state": r.state,
             "seconds": r.seconds, "attempts": r.attempts, "verdicts": r.verdicts, "usage": r.usage,
             "claim_commit": r.commit} for r in results]


CONDITIONS = {"seq": cond_seq, "shared": cond_shared, "worktree": cond_worktree, "gated": cond_gated}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", choices=CONDITIONS, required=True)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--rep", type=int, default=1)
    ap.add_argument("--suite", choices=["independent", "coupled"], default="independent",
                    help="independent: the 8 feature tasks; coupled: those plus 2 cross-cutting tasks")
    ap.add_argument("--tasks", nargs="*", default=None, help="task ids (default: all)")
    ap.add_argument("--work", default=None, help="scratch directory for the repos (default: a temp dir)")
    a = ap.parse_args()
    # Agents that commit or merge on their own get a neutral identity, not the operator's.
    os.environ.update(GIT_AUTHOR_NAME="bench-agent", GIT_AUTHOR_EMAIL="agent@bench.invalid",
                      GIT_COMMITTER_NAME="bench-agent", GIT_COMMITTER_EMAIL="agent@bench.invalid")

    dirs = ["tasks"] + (["tasks-coupled"] if a.suite == "coupled" else [])
    tasks = sorted((load(f) for d in dirs for f in glob.glob(os.path.join(HERE, d, "*.toml"))), key=lambda t: t.id)
    if a.tasks:
        tasks = [t for t in tasks if t.id in a.tasks]
    suite = "" if a.suite == "independent" else f"{a.suite}-"
    out = os.path.join(HERE, "runs", f"{a.model}-{suite}{a.condition}-r{a.rep}")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    root = tempfile.mkdtemp(prefix=f"bench-{a.condition}-", dir=a.work)
    repo = make_repo(root)
    agent = ClaudeAgent(model=a.model, extra_args=ISOLATION)
    print(f"{a.condition} rep {a.rep}: {len(tasks)} tasks, parallel {a.parallel}, model {a.model}, repo {repo}", flush=True)
    t0 = time.time()
    lanes = CONDITIONS[a.condition](repo, tasks, agent, out, a.parallel)
    wall = round(time.time() - t0, 1)
    final = git(repo, "rev-parse", "main")
    bundle = os.path.join(out, "repo.bundle")
    git(repo, "bundle", "create", bundle, "--all")
    with open(os.path.join(out, "results.json"), "w") as f:
        json.dump({"condition": a.condition if a.condition != "gated" or os.environ.get("BENCH_GATE") != "strict"
                   else "gated-strict", "suite": a.suite, "rep": a.rep, "model": a.model, "parallel": a.parallel,
                   "wall_seconds": wall, "final_commit": final, "lanes": lanes}, f, indent=1)
    print(f"{a.condition} rep {a.rep}: wall {wall}s, results in {out}")


if __name__ == "__main__":
    main()
