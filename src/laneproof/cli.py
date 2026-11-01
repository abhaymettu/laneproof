"""laneproof: run many coding agents in parallel; count only the work that lands with evidence."""
import argparse
import glob
import json
import os
import shlex
import sys
import time

from . import __version__, admission, demo
from .agents import from_spec
from .gate import verify
from .git import GitError
from .lanes import Coordinator, save_results, state_dir
from .tasks import load


def _limits(a):
    return admission.Limits(cap=a.cap, min_free_gb=a.min_free_gb, max_load_per_cpu=a.max_load, quota_cmd=a.quota_cmd)


def _registry(repo):
    return admission.Registry(state_dir(repo))


def cmd_run(a):
    files = sorted(f for p in a.tasks for f in (glob.glob(os.path.join(p, "*.toml")) if os.path.isdir(p) else [p]))
    tasks = [load(f) for f in files]
    agent = from_spec(a.agent, model=a.model, extra_args=shlex.split(a.agent_args or ""))
    coord = Coordinator(a.repo, agent, _limits(a), target=a.target, retries=a.retries, lane_timeout=a.lane_timeout,
                        union=not a.no_union)
    t0 = time.time()
    results = coord.run(tasks)
    path = save_results(coord.registry, results)
    landed = sum(r.state == "landed" for r in results)
    caught = sum(v["state"] == "rejected" for r in results for v in r.verdicts)
    print(f"\n{landed}/{len(results)} landed in {time.time() - t0:.0f}s; gate rejected {caught} 'done' claims; results {path}")
    return 0 if landed == len(results) else 1


def cmd_reserve(a):
    res = admission.reserve(_registry(a.repo), a.holder, _limits(a), a.repo)
    print(json.dumps({"id": res["id"], "token": res["token"]}))


def cmd_claim(a):
    admission.claim(_registry(a.repo), a.id, a.token)
    print(f"claimed {a.id}: start exactly one agent now")


def cmd_release(a):
    admission.release(_registry(a.repo), a.id, a.reason)
    print(f"released {a.id}")


def cmd_verify(a):
    v = verify(a.repo, _registry(a.repo), a.lane, a.worktree, load(a.task), a.target)
    print(json.dumps({"state": v.state, "commit": v.commit, "reasons": v.reasons,
                      "replays": [{k: r[k] for k in ("cmd", "ok", "exit")} for r in v.replays]}, indent=1))
    return 0 if v.landed else 1


def cmd_status(a):
    reg = _registry(a.repo).read()
    held, orphans = admission.occupancy(reg)
    print(f"reserved {len(held)}, unreserved live {len(orphans)}")
    for name, lane in sorted(reg["lanes"].items(), key=lambda kv: kv[1].get("updated", 0)):
        print(f"  {name:<30} {lane.get('state', '?'):<8} {lane.get('reservation', '')}  attempts {lane.get('attempts', 1)}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="laneproof", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, limits=False):
        sp.add_argument("--repo", default=".")
        if limits:
            sp.add_argument("--cap", type=int, default=4, help="most agents at once (default 4)")
            sp.add_argument("--min-free-gb", type=float, default=1.0)
            sp.add_argument("--max-load", type=float, default=None, help="refuse above this 1-min load per CPU")
            sp.add_argument("--quota-cmd", default=None, help="shell command; non-zero exit refuses a start")
        return sp

    sub.add_parser("demo", help="three scripted lanes in a throwaway repo (no model)").set_defaults(fn=lambda a: demo.main())
    r = common(sub.add_parser("run", help="run task files as parallel lanes"), limits=True)
    r.add_argument("tasks", nargs="+", help="task .toml files or directories of them")
    r.add_argument("--agent", default="claude", help="'claude' or a command template with {prompt} / {prompt_file}")
    r.add_argument("--model", default=None)
    r.add_argument("--agent-args", default=None, help="extra arguments for the claude adapter")
    r.add_argument("--target", default="main")
    r.add_argument("--retries", type=int, default=2, help="times a rejected lane is sent back with feedback")
    r.add_argument("--lane-timeout", type=int, default=1800)
    r.add_argument("--no-union", action="store_true",
                   help="send every merge conflict back to the lane instead of first trying a union merge the gate re-checks")
    r.set_defaults(fn=cmd_run)
    s = common(sub.add_parser("reserve", help="ask admission for one start"), limits=True)
    s.add_argument("holder")
    s.set_defaults(fn=cmd_reserve)
    s = common(sub.add_parser("claim", help="spend a reservation on exactly one start"))
    s.add_argument("id")
    s.add_argument("token")
    s.set_defaults(fn=cmd_claim)
    s = common(sub.add_parser("release", help="give a reservation back"))
    s.add_argument("id")
    s.add_argument("--reason", default="released")
    s.set_defaults(fn=cmd_release)
    s = common(sub.add_parser("verify", help="gate one lane's worktree and land it if the evidence holds"))
    s.add_argument("lane")
    s.add_argument("worktree")
    s.add_argument("task")
    s.add_argument("--target", default="main")
    s.set_defaults(fn=cmd_verify)
    common(sub.add_parser("status", help="reservations and lanes")).set_defaults(fn=cmd_status)

    a = p.parse_args(argv)
    try:
        return a.fn(a) or 0
    except admission.Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return e.code
    except GitError as e:
        if "not a git repository" in str(e):
            repo = os.path.abspath(getattr(a, "repo", "."))
            print(f"laneproof: {repo} is not a git repository; run inside one or pass --repo", file=sys.stderr)
        else:
            print(f"laneproof: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
