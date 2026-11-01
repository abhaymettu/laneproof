"""Hidden checks: the ground truth for whether a task is done. Agents never see this file.

  python3 -I checks.py REPO TASK   ->  one JSON line {"task", "passed", "failures"}
  python3 -I checks.py REPO suite  ->  whether the repository's own unittest suite passes

Every assertion maps to a criterion stated in the task file; nothing here asks for more than the spec.
"""
import importlib
import json
import os
import re
import subprocess
import sys
import textwrap

REPO = os.path.abspath(sys.argv[1])
sys.path.insert(0, REPO)
os.chdir(REPO)
FAIL = []


def check(name, fn):
    try:
        ok = fn()
    except Exception as e:  # a crash in the code under test is a failure, not a crash of the checker
        ok = False
        name += f" ({type(e).__name__}: {str(e)[:80]})"
    if ok is False:
        FAIL.append(name)


def raises(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except ValueError:
        return True
    except Exception:
        return False
    return False


def cli(*args, stdin=None):
    p = subprocess.run([sys.executable, "-m", "textkit", *args], input=stdin or "", capture_output=True, text=True,
                       timeout=30, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return p.returncode, p.stdout, p.stderr


def cli_error(*args):
    code, _, err = cli(*args)
    return code == 2 and "error:" in err


def docs(cmd, module):
    readme = open("README.md").read()
    check("c4 README row", lambda: re.search(rf"^\| `{cmd}` \|", readme, re.M) is not None)
    check("c4 commands lists it", lambda: cmd in cli("commands")[1].split())
    log = open("CHANGELOG.md").read()
    unreleased = log.split("## Unreleased", 1)[1].split("\n## ", 1)[0] if "## Unreleased" in log else ""
    check("c5 changelog bullet", lambda: re.search(rf"^- .*`{cmd}`", unreleased, re.M) is not None)
    check("c6 test file", lambda: os.path.exists(f"tests/test_{module}.py"))


def mod(name):
    return importlib.import_module(f"textkit.{name}")


def slug():
    m = mod("slug")
    check("c1 accents", lambda: m.slugify("Héllo, Wörld!") == "hello-world")
    check("c1 runs", lambda: m.slugify("  C++ & Rust  ") == "c-rust")
    check("c1 sep", lambda: m.slugify("a b", sep="_") == "a_b")
    check("c1 digits", lambda: m.slugify("Top 10 -- list") == "top-10-list")
    check("c2 empty", lambda: raises(m.slugify, "!!!") and raises(m.slugify, ""))
    check("c3 cli", lambda: cli("slug", "Héllo, Wörld!")[1].strip() == "hello-world")
    check("c3 cli sep", lambda: cli("slug", "a b", "--sep", "_")[1].strip() == "a_b")
    check("c3 cli error", lambda: cli_error("slug", "!!!"))
    docs("slug", "slug")


ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
         (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]


def _roman(n):
    out = ""
    for v, s in ROMAN:
        while n >= v:
            out, n = out + s, n - v
    return out


def roman():
    m = mod("roman")
    check("c1 to_roman all", lambda: all(m.to_roman(n) == _roman(n) for n in range(1, 4000)))
    check("c1 round trip", lambda: all(m.from_roman(_roman(n)) == n for n in range(1, 4000)))
    check("c2 to_roman range", lambda: raises(m.to_roman, 0) and raises(m.to_roman, 4000) and raises(m.to_roman, -5))
    check("c2 non-canonical", lambda: all(raises(m.from_roman, s) for s in ("IIII", "IC", "VX", "mcm", "", "IIV", "XM")))
    check("c3 cli to", lambda: cli("roman", "1994")[1].strip() == "MCMXCIV")
    check("c3 cli from", lambda: cli("roman", "MCMXCIV")[1].strip() == "1994")
    check("c3 cli error", lambda: cli_error("roman", "IIII"))
    docs("roman", "roman")


def duration():
    m = mod("duration")
    cases = {"1h30m": 5400, "45s": 45, "2h5s": 7205, "90m": 5400, "1h": 3600, "0s": 0, "1h2m3s": 3723}
    check("c1 parse", lambda: all(m.parse_duration(k) == v for k, v in cases.items()))
    check("c1 format", lambda: [m.format_duration(x) for x in (5400, 7205, 59, 0, 3723, 3600, 60)]
          == ["1h30m", "2h5s", "59s", "0s", "1h2m3s", "1h", "1m"])
    check("c2 parse invalid", lambda: all(raises(m.parse_duration, s) for s in ("", "1x", "30m1h", "1h1h", "1.5h", "h", "1h 30m")))
    check("c2 format negative", lambda: raises(m.format_duration, -1))
    check("c3 cli parse", lambda: cli("dur", "1h30m")[1].strip() == "5400")
    check("c3 cli format", lambda: cli("dur", "5400")[1].strip() == "1h30m")
    check("c3 cli error", lambda: cli_error("dur", "30m1h"))
    docs("dur", "duration")


def _wrap(text, width):
    paras = [p for p in re.split(r"\n[ \t]*\n", text.strip()) if p.strip()]
    return "\n\n".join(textwrap.fill(" ".join(p.split()), width, break_long_words=False, break_on_hyphens=False)
                       for p in paras)


def wrap():
    m = mod("wrap")
    text = "The quick brown fox\njumps over   the lazy dog.\n\n\n  Second paragraph with a supercalifragilistic word and well-known-hyphens.\n"
    check("c1 fill", lambda: all(m.wrap(text, w) == _wrap(text, w) for w in (8, 12, 20, 40)))
    check("c1 long word", lambda: m.wrap("a verylongword b", 4) == "a\nverylongword\nb")
    check("c1 blank lines with spaces", lambda: m.wrap("a b\n   \nc", 10) == "a b\n\nc")
    check("c2 width", lambda: raises(m.wrap, "x", 0))
    check("c3 cli", lambda: cli("wrap", "aaa bbb ccc", "--width", "7")[1] == "aaa bbb\nccc\n")
    check("c3 cli error", lambda: cli_error("wrap", "x", "--width", "0"))
    docs("wrap", "wrap")


def topwords():
    m = mod("topwords")
    check("c1 basic", lambda: [tuple(x) for x in m.top_words("b a b c a b", 2)] == [("b", 3), ("a", 2)])
    check("c1 ties alpha", lambda: [tuple(x) for x in m.top_words("z y x z y x w", 3)] == [("x", 2), ("y", 2), ("z", 2)])
    check("c1 split", lambda: dict(m.top_words("Don't stop, DON'T! t42t", 5)) == {"don": 2, "t": 4, "stop": 1})
    check("c1 fewer than k", lambda: len(m.top_words("one two", 10)) == 2)
    check("c2 k", lambda: raises(m.top_words, "x", 0))
    check("c3 cli", lambda: cli("top", "b a b c a b", "--k", "2")[1] == "b 3\na 2\n")
    check("c3 cli error", lambda: cli_error("top", "x", "--k", "0"))
    docs("top", "topwords")


def rot():
    m = mod("rot")
    check("c1 rot13", lambda: m.rot("Hello, World!", 13) == "Uryyb, Jbeyq!")
    check("c1 negative", lambda: m.rot("abc", -1) == "zab")
    check("c1 large", lambda: m.rot("Zz", 53) == "Aa")
    check("c1 non-ascii", lambda: m.rot("é1 ", 3) == "é1 ")
    check("c2 types", lambda: raises(m.rot, "a", 1.5) and raises(m.rot, "a", "1") and raises(m.rot, "a", True))
    check("c3 cli default", lambda: cli("rot", "Hello, World!")[1].strip() == "Uryyb, Jbeyq!")
    check("c3 cli n", lambda: cli("rot", "abc", "--n", "-1")[1].strip() == "zab")
    check("c3 cli error", lambda: cli_error("rot", "abc", "--n", "1.5"))
    docs("rot", "rot")


def mdtable():
    m = mod("mdtable")
    check("c1 table", lambda: m.to_markdown([["a", "b"], ["1", "2"]]) == "| a | b |\n| --- | --- |\n| 1 | 2 |")
    check("c1 strip escape", lambda: m.to_markdown([[" x ", "y|z"], ["a|", " b"]]) == "| x | y\\|z |\n| --- | --- |\n| a\\| | b |")
    check("c1 header only", lambda: m.to_markdown([["h"]]) == "| h |\n| --- |")
    check("c2 invalid", lambda: raises(m.to_markdown, []) and raises(m.to_markdown, [[]]) and raises(m.to_markdown, [["a", "b"], ["1"]]))
    check("c3 cli", lambda: cli("mdtable", stdin="a,b\n1,2\n")[1] == "| a | b |\n| --- | --- |\n| 1 | 2 |\n")
    check("c3 cli error", lambda: cli("mdtable", stdin="a,b\n1\n")[0] == 2 and "error:" in cli("mdtable", stdin="a,b\n1\n")[2])
    docs("mdtable", "mdtable")


def near():
    m = mod("near")
    check("c1 levenshtein", lambda: (m.levenshtein("kitten", "sitting"), m.levenshtein("", "abc"), m.levenshtein("same", "same"),
                                     m.levenshtein("flaw", "lawn")) == (3, 3, 0, 2))
    check("c1 closest", lambda: m.closest("helo", ["help", "hello"]) == "help")
    check("c1 closest none", lambda: m.closest("zzzz", ["hello"]) is None and m.closest("a", []) is None)
    check("c1 max", lambda: m.closest("abcd", ["abxy"], max_distance=1) is None and m.closest("abcd", ["abxy"], max_distance=2) == "abxy")
    check("c2 max negative", lambda: raises(m.closest, "a", ["a"], -1))
    check("c3 cli", lambda: cli("near", "helo", "hello", "help")[1].strip() == "hello")
    check("c3 cli no match", lambda: (lambda r: r[0] == 1 and "no match" in r[2] and r[1] == "")(cli("near", "zzzz", "hello")))
    check("c3 cli error", lambda: cli_error("near", "helo", "hello", "--max", "-1"))
    docs("near", "near")


def readme_cells():
    return dict(re.findall(r"^\| `([a-z0-9]+)` \| (.*?) \|\s*$", open("README.md").read(), re.M))


def registry():
    return importlib.import_module("textkit.cli").COMMANDS


def docsync():
    check("c1 test file", lambda: os.path.exists("tests/test_docsync.py"))
    cells, reg = readme_cells(), registry()
    check("c2 every cell equals help", lambda: all(cells.get(n) == c["help"] for n, c in reg.items()))

    def catches_drift():
        import shutil
        import tempfile
        tmp = tempfile.mkdtemp()
        shutil.copytree(REPO, os.path.join(tmp, "r"), ignore=shutil.ignore_patterns(".git", "__pycache__"))
        path = os.path.join(tmp, "r", "README.md")
        text = open(path).read()
        open(path, "w").write(re.sub(r"^(\| `shout` \| ).*?( \|)$", r"\1something else\2", text, count=1, flags=re.M))
        p = subprocess.run([sys.executable, "-m", "unittest", "-q", "tests.test_docsync"], cwd=os.path.join(tmp, "r"),
                           capture_output=True, text=True, timeout=120)
        return p.returncode != 0
    check("c1 test catches drift", catches_drift)
    log = open("CHANGELOG.md").read()
    unreleased = log.split("## Unreleased", 1)[1].split("\n## ", 1)[0] if "## Unreleased" in log else ""
    check("c3 changelog bullet", lambda: re.search(r"^- .*`test_docsync", unreleased, re.M) is not None)


# One plain invocation per command a lane might have added; commands not in the registry are skipped.
SAMPLES = {"shout": (["hi"], None), "count": (["a b c"], None), "commands": ([], None), "slug": (["A B"], None),
           "roman": (["12"], None), "dur": (["90s"], None), "wrap": (["aa bb", "--width", "2"], None),
           "top": (["a a b"], None), "rot": (["abc"], None), "mdtable": ([], "a,b\n1,2\n"),
           "near": (["helo", "hello"], None)}


def jsonout():
    def same_as_plain(name):
        args, stdin = SAMPLES[name]
        code, out, _ = cli(name, *args, stdin=stdin)
        jcode, jout, _ = cli("--json", name, *args, stdin=stdin)
        return jcode == code and json.loads(jout) == {"command": name, "exit": code, "output": out.splitlines()}
    check("c1 shout", lambda: json.loads(cli("--json", "shout", "hi")[1]) == {"command": "shout", "exit": 0, "output": ["HI"]})
    present = [n for n in registry() if n in SAMPLES]
    for n in present:
        check(f"c2 --json {n}", lambda n=n: same_as_plain(n))

    def error_case():
        if "roman" in registry():
            args = ["roman", "IIII"]
        else:
            return True  # no command with a ValueError path to try
        code, out, err = cli("--json", *args)
        return code == 2 and "error:" in err and json.loads(out)["exit"] == 2
    check("c3 error", error_case)
    check("c3 plain unchanged", lambda: cli("shout", "hi")[1] == "HI\n")
    conv = open("README.md").read().split("## Conventions", 1)[-1].split("\n## ", 1)[0]
    check("c4 README conventions", lambda: "--json" in conv)
    log = open("CHANGELOG.md").read()
    unreleased = log.split("## Unreleased", 1)[1].split("\n## ", 1)[0] if "## Unreleased" in log else ""
    check("c5 changelog bullet", lambda: re.search(r"^- .*`--json`", unreleased, re.M) is not None)
    check("c6 test file", lambda: os.path.exists("tests/test_json.py"))


def suite():
    p = subprocess.run([sys.executable, "-m", "unittest", "-q"], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if p.returncode != 0:
        FAIL.append("unittest suite: " + p.stderr.strip().splitlines()[-1][:120] if p.stderr.strip() else "unittest suite failed")


if __name__ == "__main__":
    task = sys.argv[2]
    try:
        globals()[task]()
    except Exception as e:  # the module is missing or does not import
        FAIL.append(f"setup ({type(e).__name__}: {str(e)[:100]})")
    print(json.dumps({"task": task, "passed": not FAIL, "failures": FAIL}))
