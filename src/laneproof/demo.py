"""`laneproof demo`: three scripted toy lanes in a throwaway repo. No model, no network, about ten seconds.

  rename   renames add() to plus() and lands first.
  double   adds double() on top of add(). Its test passes on its own branch, so it says done. On main,
           after rename landed, it fails. The gate catches it; the lane merges main, fixes it, lands.
  docs     claims its docs are done with a CLAIM and no FACT. The gate refuses until it proves it.

The cap is 2, so the third lane waits for headroom, and a replayed start of a used reservation is refused.
"""
import os
import subprocess
import sys
import tempfile
import textwrap

from . import admission
from .agents import CommandAgent
from .lanes import Coordinator
from .tasks import Criterion, Task

MATHX = "def add(a, b):\n    return a + b\n"
TEST = "import unittest\nfrom toy.mathx import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"

FAKE_AGENT = r'''
import json, os, re, subprocess, sys, time
prompt = open(sys.argv[1]).read()
retry = "did not accept" in prompt
def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    open(path, "w").write(text)
def receipt(claims):
    json.dump({"status": "done", "summary": "toy lane", "claims": claims}, open(".laneproof-receipt.json", "w"))
fact = lambda c, cmd: {"criterion": c, "label": "FACT", "cmd": cmd}
if "Rename add" in prompt:
    write("toy/mathx.py", "def plus(a, b):\n    return a + b\n")
    write("tests/test_mathx.py", open("tests/test_mathx.py").read().replace("add", "plus"))
    receipt([fact("c1", "python3 -m unittest -q")])
elif "Add double" in prompt:
    if not retry:
        time.sleep(2)
        write("toy/double.py", "from toy.mathx import add\n\ndef double(x):\n    return add(x, x)\n")
        write("tests/test_double.py", "import unittest\nfrom toy.double import double\n\nclass T(unittest.TestCase):\n    def test(self):\n        self.assertEqual(double(4), 8)\n")
    else:
        subprocess.run(["git", "merge", "-q", "--no-edit", "main"], capture_output=True)
        write("toy/double.py", "from toy.mathx import plus\n\ndef double(x):\n    return plus(x, x)\n")
    receipt([fact("c1", "python3 -m unittest -q tests.test_double")])
elif "Document" in prompt:
    write("DOCS.md", "toy.mathx.plus(a, b) returns a + b.\n")
    receipt([fact("c1", "grep -q plus DOCS.md")] if retry else [{"label": "CLAIM", "text": "docs are clear"}])
'''


def build_repo(root):
    repo = os.path.join(root, "toy-repo")
    os.makedirs(os.path.join(repo, "toy"))
    os.makedirs(os.path.join(repo, "tests"))
    for path, text in {"toy/__init__.py": "", "toy/mathx.py": MATHX, "tests/__init__.py": "",
                       "tests/test_mathx.py": TEST, ".gitignore": "__pycache__/\n"}.items():
        with open(os.path.join(repo, path), "w") as f:
            f.write(text)
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"],
                ["-c", "user.name=demo", "-c", "user.email=demo@localhost", "commit", "-q", "-m", "toy repo"]):
        subprocess.run(["git", "-C", repo, *cmd], check=True)
    return repo


TASKS = [
    Task("rename", "rename add to plus", "Rename add to plus everywhere in the toy package and its tests.",
         [Criterion("c1", "the test suite passes")], check="python3 -m unittest -q"),
    Task("double", "add double()", "Add double(x) in toy/double.py, built on the existing adder, with a test.",
         [Criterion("c1", "double(4) == 8, shown by a test")], check="python3 -m unittest -q"),
    Task("docs", "document mathx", "Document the toy.mathx public function in DOCS.md.",
         [Criterion("c1", "DOCS.md names the public function")], scope=["DOCS.md"]),
]


def main():
    root = tempfile.mkdtemp(prefix="laneproof-demo-")
    os.environ["LANEPROOF_HOME"] = os.path.join(root, "home")
    repo = build_repo(root)
    agent_py = os.path.join(root, "fake_agent.py")
    with open(agent_py, "w") as f:
        f.write(FAKE_AGENT)
    agent = CommandAgent(f"{sys.executable} {agent_py} {{prompt_file}}")
    print(f"demo repo: {repo}\n3 lanes, cap 2, scripted agents (no model calls)\n")
    coord = Coordinator(repo, agent, admission.Limits(cap=2, min_free_gb=0.1), retries=2, poll=0.5,
                        say=lambda s: print("  " + s))
    results = coord.run(TASKS)

    print("\nreplaying the first lane's start (a crashed coordinator restarting, say):")
    first = coord.registry.read()["reservations"][0]
    try:
        admission.claim(coord.registry, first["id"], first["token"])
        print("  BUG: a second start was allowed")
    except admission.Refused as e:
        print(f"  refused: {e}")

    print("\nresult")
    for r in results:
        rejected = sum(v["state"] == "rejected" for v in r.verdicts)
        print(f"  {r.task:<8} {r.state:<7} attempts {r.attempts}  false 'done' claims caught {rejected}")
    log = subprocess.run(["git", "-C", repo, "log", "--oneline", "main"], capture_output=True, text=True).stdout
    print("\nmain now:\n" + textwrap.indent(log.strip(), "  "))
    test = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=repo, capture_output=True, text=True)
    print(f"\nunit tests on main: {'pass' if test.returncode == 0 else 'FAIL'}")
    ok = all(r.state == "landed" for r in results) and test.returncode == 0
    return 0 if ok else 1
