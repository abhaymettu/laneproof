"""textkit command line: `python3 -m textkit <command> ...`.

Every command is registered with @command. Library functions raise ValueError on bad input;
main() turns that into `error: <message>` on stderr and exit status 2.
"""
import argparse
import sys

from . import core

COMMANDS = {}


def command(name, help, args=()):
    """Register a subcommand. args is a list of (name_or_flag, add_argument kwargs) pairs."""
    def deco(fn):
        COMMANDS[name] = {"help": help, "args": args, "run": fn}
        return fn
    return deco


@command("shout", "upper-case the text", [("text", {})])
def _shout(a):
    print(core.shout(a.text))
    return 0


@command("count", "count the words in the text", [("text", {})])
def _count(a):
    print(core.count_words(a.text))
    return 0


@command("commands", "list every command")
def _commands(a):
    for name in sorted(COMMANDS):
        print(name)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="textkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, c in COMMANDS.items():
        sp = sub.add_parser(name, help=c["help"])
        for flag, kw in c["args"]:
            sp.add_argument(flag, **kw)
    a = p.parse_args(argv)
    try:
        return COMMANDS[a.cmd]["run"](a)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
