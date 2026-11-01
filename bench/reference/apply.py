"""Write reference solutions into a textkit repo, to prove the hidden checks are passable and fair.

  python3 bench/reference/apply.py REPO [TASK ...]
"""
import os
import sys

MODULES = {
"slug": ("slug", '''import re
import unicodedata


def slugify(text, sep="-"):
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    out = re.sub(r"[^a-z0-9]+", sep, ascii_text.lower()).strip(sep)
    if not out:
        raise ValueError("nothing left to slugify")
    return out
''', '''@command("slug", "turn text into a URL slug", [("text", {}), ("--sep", {"default": "-"})])
def _slug(a):
    from .slug import slugify
    print(slugify(a.text, a.sep))
    return 0
'''),
"roman": ("roman", '''VALUES = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
          (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def to_roman(n):
    if not isinstance(n, int) or not 1 <= n <= 3999:
        raise ValueError(f"out of range: {n}")
    out = ""
    for v, s in VALUES:
        while n >= v:
            out, n = out + s, n - v
    return out


def from_roman(s):
    total, i = 0, 0
    for v, sym in VALUES:
        while s.startswith(sym, i):
            total, i = total + v, i + len(sym)
    if i != len(s) or not 1 <= total <= 3999 or to_roman(total) != s:
        raise ValueError(f"not a canonical numeral: {s!r}")
    return total
''', '''@command("roman", "convert to or from roman numerals", [("value", {})])
def _roman(a):
    from .roman import from_roman, to_roman
    print(to_roman(int(a.value)) if a.value.isdigit() else from_roman(a.value))
    return 0
'''),
"duration": ("dur", '''import re

PART = re.compile(r"(?:(\\d+)h)?(?:(\\d+)m)?(?:(\\d+)s)?")


def parse_duration(s):
    m = PART.fullmatch(s)
    if not s or not m or not any(m.groups()):
        raise ValueError(f"bad duration: {s!r}")
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def format_duration(seconds):
    if seconds < 0:
        raise ValueError("negative duration")
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    out = (f"{h}h" if h else "") + (f"{m}m" if m else "") + (f"{s}s" if s else "")
    return out or "0s"
''', '''@command("dur", "parse or format a duration", [("value", {})])
def _dur(a):
    from .duration import format_duration, parse_duration
    print(format_duration(int(a.value)) if a.value.isdigit() else parse_duration(a.value))
    return 0
'''),
"wrap": ("wrap", '''import re
import textwrap


def wrap(text, width):
    if width < 1:
        raise ValueError("width must be at least 1")
    paras = [p for p in re.split(r"\\n[ \\t]*\\n", text.strip()) if p.strip()]
    return "\\n\\n".join(textwrap.fill(" ".join(p.split()), width, break_long_words=False, break_on_hyphens=False)
                         for p in paras)
''', '''@command("wrap", "word-wrap the text", [("text", {}), ("--width", {"type": int, "default": 40})])
def _wrap(a):
    from .wrap import wrap
    print(wrap(a.text, a.width))
    return 0
'''),
"topwords": ("top", '''import re
from collections import Counter


def top_words(text, k):
    if k < 1:
        raise ValueError("k must be at least 1")
    counts = Counter(re.findall(r"[a-z]+", text.lower()))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]
''', '''@command("top", "most frequent words", [("text", {}), ("--k", {"type": int, "default": 3})])
def _top(a):
    from .topwords import top_words
    for word, n in top_words(a.text, a.k):
        print(word, n)
    return 0
'''),
"rot": ("rot", '''def rot(text, n):
    if not isinstance(n, int) or isinstance(n, bool):
        raise ValueError("n must be an int")
    out = []
    for ch in text:
        if "a" <= ch <= "z":
            out.append(chr((ord(ch) - 97 + n) % 26 + 97))
        elif "A" <= ch <= "Z":
            out.append(chr((ord(ch) - 65 + n) % 26 + 65))
        else:
            out.append(ch)
    return "".join(out)
''', '''@command("rot", "rotate letters (rot13 by default)", [("text", {}), ("--n", {"type": int, "default": 13})])
def _rot(a):
    from .rot import rot
    print(rot(a.text, a.n))
    return 0
'''),
"mdtable": ("mdtable", '''def to_markdown(rows):
    if not rows or not rows[0]:
        raise ValueError("no header")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise ValueError("ragged rows")
    line = lambda r: "| " + " | ".join(c.strip().replace("|", "\\\\|") for c in r) + " |"
    return "\\n".join([line(rows[0]), "| " + " | ".join(["---"] * width) + " |", *map(line, rows[1:])])
''', '''@command("mdtable", "CSV on stdin to a markdown table")
def _mdtable(a):
    import csv
    from .mdtable import to_markdown
    print(to_markdown(list(csv.reader(sys.stdin))))
    return 0
'''),
"near": ("near", '''def levenshtein(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def closest(word, candidates, max_distance=2):
    if max_distance < 0:
        raise ValueError("max_distance must be >= 0")
    best = None
    for c in candidates:
        d = levenshtein(word, c)
        if d <= max_distance and (best is None or d < best[0]):
            best = (d, c)
    return best and best[1]
''', '''@command("near", "closest candidate to a word",
         [("word", {}), ("candidates", {"nargs": "+"}), ("--max", {"type": int, "default": 2})])
def _near(a):
    from .near import closest
    m = closest(a.word, a.candidates, a.max)
    if m is None:
        print("no match", file=sys.stderr)
        return 1
    print(m)
    return 0
'''),
}


DOCSYNC_TEST = '''import pathlib
import re
import unittest

from textkit.cli import COMMANDS

README = (pathlib.Path(__file__).resolve().parent.parent / "README.md").read_text()


class DocSync(unittest.TestCase):
    def test_cells_equal_help(self):
        cells = dict(re.findall(r"^\\| `([a-z0-9]+)` \\| (.*?) \\|\\s*$", README, re.M))
        for name, c in COMMANDS.items():
            self.assertEqual(cells.get(name), c["help"], name)
'''

JSON_MAIN = '''def main(argv=None):
    import contextlib
    import io
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = bool(argv) and argv[0] == "--json"
    if as_json:
        argv = argv[1:]
    p = argparse.ArgumentParser(prog="textkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, c in COMMANDS.items():
        sp = sub.add_parser(name, help=c["help"])
        for flag, kw in c["args"]:
            sp.add_argument(flag, **kw)
    a = p.parse_args(argv)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf if as_json else sys.stdout):
            code = COMMANDS[a.cmd]["run"](a)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        code = 2
    if as_json:
        print(json.dumps({"command": a.cmd, "exit": code, "output": buf.getvalue().splitlines()}))
    return code
'''


def changelog(repo, line):
    log = os.path.join(repo, "CHANGELOG.md")
    text = open(log).read().replace("## Unreleased\n\n", f"## Unreleased\n\n- {line}\n", 1)
    open(log, "w").write(text)


def apply_coupled(repo, task):
    if task == "docsync":
        open(os.path.join(repo, "tests", "test_docsync.py"), "w").write(DOCSYNC_TEST)
        changelog(repo, "README cells must match help strings (`test_docsync`).")
    else:
        path = os.path.join(repo, "textkit", "cli.py")
        src = open(path).read()
        open(path, "w").write(src[:src.index("def main(argv=None):")] + JSON_MAIN)
        readme = os.path.join(repo, "README.md")
        text = open(readme).read().replace("## Commands", "- `--json` before the command prints a JSON object.\n\n## Commands", 1)
        open(readme, "w").write(text)
        changelog(repo, "Global `--json` output.")
        open(os.path.join(repo, "tests", "test_json.py"), "w").write(
            "import unittest\n\nfrom textkit.cli import main\n\n\nclass J(unittest.TestCase):\n"
            "    def test_runs(self):\n        self.assertEqual(main([\"--json\", \"commands\"]), 0)\n")


def apply(repo, task):
    if task in ("docsync", "jsonout"):
        return apply_coupled(repo, task)
    cmd, body, cli = MODULES[task]
    module = task
    with open(os.path.join(repo, "textkit", f"{module}.py"), "w") as f:
        f.write(body)
    path = os.path.join(repo, "textkit", "cli.py")
    src = open(path).read()
    src = src.replace('\n\n@command("commands"', "\n\n" + cli + '\n\n@command("commands"', 1)
    open(path, "w").write(src)
    readme = os.path.join(repo, "README.md")
    help_text = cli.split('", "', 1)[1].split('"', 1)[0]
    text = open(readme).read().rstrip("\n") + f"\n| `{cmd}` | {help_text} |\n"
    open(readme, "w").write(text)
    log = os.path.join(repo, "CHANGELOG.md")
    text = open(log).read().replace("## Unreleased\n\n", f"## Unreleased\n\n- Added `{cmd}`.\n", 1)
    open(log, "w").write(text)
    with open(os.path.join(repo, "tests", f"test_{module}.py"), "w") as f:
        f.write(f"import unittest\n\nimport textkit.{module}\n\n\nclass T(unittest.TestCase):\n"
                f"    def test_imports(self):\n        self.assertTrue(textkit.{module})\n")


if __name__ == "__main__":
    repo, tasks = sys.argv[1], sys.argv[2:] or [*MODULES, "docsync", "jsonout"]
    for t in tasks:
        apply(repo, t)
