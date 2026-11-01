"""Redact raw run logs before they are committed: local paths, the operator's name, email and home directory.

  python3 bench/redact.py            (rewrites bench/runs/*/*.jsonl and results.json in place; idempotent)
"""
import glob
import os
import re
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
home = os.path.expanduser("~")
user = os.path.basename(home)
who = [subprocess.run(["git", "config", k], capture_output=True, text=True).stdout.strip() for k in ("user.name", "user.email")]

RULES = [
    (re.compile(r"/private/tmp/[^\s\"']*?/work/"), "$WORK/"),
    (re.compile(r"/(private/)?var/folders/[^\s\"']+?/T/"), "$TMP/"),
    (re.compile(re.escape(home)), "~"),
    (re.compile(r"-Users-" + re.escape(user) + r"-[A-Za-z0-9-]*"), "-redacted"),
    (re.compile(r"/tmp/claude-\d+/[^\s\"']*"), "$TMP/claude"),
] + [(re.compile(re.escape(w)), "<redacted>") for w in who if w]


def redact(text):
    for pat, rep in RULES:
        text = pat.sub(rep, text)
    return text


if __name__ == "__main__":
    n = 0
    for path in [p for d in ("runs", "runs-excluded") for g in ("*.jsonl", "results.json") for p in glob.glob(os.path.join(HERE, d, "*", g))]:
        text = open(path, encoding="utf-8", errors="replace").read()
        new = redact(text)
        if new != text:
            open(path, "w").write(new)
            n += 1
    print(f"redacted {n} files")
