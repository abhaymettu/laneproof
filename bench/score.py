"""Score benchmark runs against the hidden checks and write bench/results.md and bench/results.json.

  python3 bench/score.py [RUN_DIR ...]     (default: every bench/runs/*/results.json)

Per lane:
  claimed done   the agent said `STATUS: done` (gated: the gate landed it)
  right at claim the hidden checks pass on the lane's own snapshot when it claimed
  truly done     the hidden checks pass on the final main branch
  false done     claimed done, but not truly done: the claim reached main and was wrong
  said not done, is   the agent reported blocked (or never finished), yet the task is done on final main
  clobbered      right at claim, wrong on final main: someone else's work broke or lost it
  gate bounces   (gated) done claims the gate sent back: conflicts, missing evidence, or checks failing on the merge
  wrong caught   (gated) bounces where the lane's own branch also failed the hidden checks: a wrong claim stopped
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKS = os.path.join(HERE, "hidden", "checks.py")
ORDER = ["seq", "shared", "worktree", "gated-strict", "gated"]


def hidden(clone, commit, task, cache):
    key = (commit, task)
    if key not in cache:
        wt = tempfile.mkdtemp(prefix="score-")
        subprocess.run(["git", "-C", clone, "worktree", "add", "-q", "--detach", wt, commit], check=True, capture_output=True)
        p = subprocess.run([sys.executable, "-I", CHECKS, wt, task], capture_output=True, text=True, timeout=600)
        subprocess.run(["git", "-C", clone, "worktree", "remove", "--force", wt], capture_output=True)
        cache[key] = json.loads(p.stdout.strip().splitlines()[-1])
    return cache[key]


def collisions(log):
    """Tool errors from another agent changing a file under this one (shared checkout only)."""
    n = 0
    try:
        for line in open(log):
            if '"is_error":true' in line.replace(" ", "") and re.search(r"modified since|has been modified", line):
                n += 1
    except OSError:
        pass
    return n


def score_run(run_dir):
    res = json.load(open(os.path.join(run_dir, "results.json")))
    clone = tempfile.mkdtemp(prefix="score-clone-")
    subprocess.run(["git", "clone", "-q", "--mirror", os.path.join(run_dir, "repo.bundle"), os.path.join(clone, ".git")],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", clone, "config", "--bool", "core.bare", "false"], check=True)
    cache, lanes = {}, []
    final = res["final_commit"]
    for l in res["lanes"]:
        t = l["task"]
        fin = hidden(clone, final, t, cache)
        at_claim = hidden(clone, l["claim_commit"], t, cache) if l.get("claim_commit") else {"passed": False}
        claimed = l["claim"] == "done"
        rejected = [v for v in l.get("verdicts", []) if v["state"] == "rejected"]
        caught_false = sum(not hidden(clone, v["commit"], t, cache)["passed"] for v in rejected if v.get("commit"))
        u = l.get("usage") or {}
        lanes.append({
            "task": t, "claimed_done": claimed, "right_at_claim": at_claim["passed"], "truly_done": fin["passed"],
            "false_done": claimed and not fin["passed"], "false_not_done": not claimed and fin["passed"], "clobbered": at_claim["passed"] and not fin["passed"],
            "final_failures": fin["failures"], "caught": len(rejected), "caught_wrong_alone": caught_false,
            "reject_kinds": [kind(v) for v in rejected], "conflicts": len(l.get("conflicts", [])),
            "collisions": collisions(os.path.join(run_dir, f"{t}.jsonl")), "seconds": l["seconds"],
            "turns": u.get("turns", 0), "runs": u.get("runs", 0),
            "tokens": sum(u.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")),
            "output_tokens": u.get("output_tokens", 0),
        })
    suite = hidden(clone, final, "suite", cache)
    return {"condition": res["condition"], "suite": res.get("suite", "independent"), "model": res["model"], "rep": res["rep"], "parallel": res["parallel"],
            "wall_seconds": res["wall_seconds"], "suite_green": suite["passed"], "lanes": lanes}


def kind(v):
    text = " ".join(v.get("reasons", []))
    if "conflicts with" in text:
        return "conflict"
    if "no FACT claim" in text or "no receipt" in text or "not valid JSON" in text:
        return "missing evidence"
    if "outside this task's scope" in text:
        return "scope"
    if "coordinator check" in text and "your FACT" not in text:
        return "coordinator check failed on merge"
    return "FACT failed on merge"


def table(runs):
    groups = defaultdict(list)
    for r in runs:
        groups[(r["model"], r["suite"], r["condition"])].append(r)
    rows, out = [], []
    for (model, suite, cond) in sorted(groups, key=lambda k: (k[0], k[1], ORDER.index(k[2]))):
        g = groups[(model, suite, cond)]
        lanes = [l for r in g for l in r["lanes"]]
        n = len(lanes)
        row = {
            "model": model, "suite": suite, "condition": cond, "reps": len(g), "tasks": n,
            "wall_s_mean": round(sum(r["wall_seconds"] for r in g) / len(g)),
            "truly_done": sum(l["truly_done"] for l in lanes),
            "claimed_done": sum(l["claimed_done"] for l in lanes),
            "false_done": sum(l["false_done"] for l in lanes),
            "false_not_done": sum(l["false_not_done"] for l in lanes),
            "clobbered": sum(l["clobbered"] for l in lanes),
            "caught": sum(l["caught"] for l in lanes),
            "caught_wrong_alone": sum(l["caught_wrong_alone"] for l in lanes),
            "conflicts": sum(l["conflicts"] for l in lanes),
            "collisions": sum(l["collisions"] for l in lanes),
            "suite_green": sum(r["suite_green"] for r in g),
            "turns": sum(l["turns"] for l in lanes),
            "tokens_m": round(sum(l["tokens"] for l in lanes) / 1e6, 2),
            "output_tokens_k": round(sum(l["output_tokens"] for l in lanes) / 1e3, 1),
            "reject_kinds": dict(sorted(_count(k for l in lanes for k in l["reject_kinds"]).items())),
        }
        rows.append(row)
    out.append("| model | suite | condition | reps | truly done | said done, isn't | said not done, is | of those, right at claim then lost or broken "
               "| gate bounces | wrong claims the gate caught | main's tests green | wall clock (mean) | turns | tokens (M, incl. cache) |")
    out.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in rows:
        gated = r["condition"].startswith("gated")
        out.append(f"| {r['model']} | {r['suite']} | {r['condition']} | {r['reps']} | {r['truly_done']}/{r['tasks']} | {r['false_done']} | {r['false_not_done']} "
                   f"| {r['clobbered']} | {r['caught'] if gated else 'n/a'} | {r['caught_wrong_alone'] if gated else 'n/a'} "
                   f"| {r['suite_green']}/{r['reps']} | {r['wall_s_mean']}s | {r['turns']} | {r['tokens_m']} |")
    return rows, "\n".join(out)


def _count(it):
    d = defaultdict(int)
    for x in it:
        d[x] += 1
    return d


def main():
    dirs = sys.argv[1:] or sorted(os.path.dirname(p) for p in glob.glob(os.path.join(HERE, "runs", "*", "results.json")))
    runs = [score_run(d) for d in dirs]
    rows, md = table(runs)
    detail = []
    for r in runs:
        for l in r["lanes"]:
            if l["false_done"] or l["clobbered"] or l["caught"] or not l["truly_done"]:
                detail.append(f"- {r['model']} {r['suite']} {r['condition']} r{r['rep']} `{l['task']}`: claimed_done={l['claimed_done']} "
                              f"right_at_claim={l['right_at_claim']} truly_done={l['truly_done']} caught={l['caught']} "
                              f"{l['reject_kinds'] or ''} final failures: {', '.join(l['final_failures'][:4]) or 'none'}")
    with open(os.path.join(HERE, "results.json"), "w") as f:
        json.dump({"summary": rows, "runs": runs}, f, indent=1)
    with open(os.path.join(HERE, "results.md"), "w") as f:
        f.write("# Benchmark results\n\nGenerated by `python3 bench/score.py` from the raw runs in `bench/runs/`.\n\n")
        f.write(md + "\n\n## Gate rejections by kind\n\n")
        for r in rows:
            if r["condition"].startswith("gated"):
                f.write(f"- {r['model']} {r['suite']}: {r['reject_kinds'] or 'none'}; rejected claims whose own branch already failed "
                        f"the hidden checks: {r['caught_wrong_alone']}/{r['caught']}\n")
        f.write("\n## Every lane that was not clean\n\n" + ("\n".join(detail) or "none") + "\n")
    print(md)


if __name__ == "__main__":
    main()
