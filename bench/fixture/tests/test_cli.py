import contextlib
import io
import unittest

from textkit.cli import main


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(run("shout", "hey"), (0, "HEY\n", ""))

    def test_commands_lists_itself(self):
        code, out, _ = run("commands")
        self.assertEqual(code, 0)
        self.assertIn("commands", out.split())
