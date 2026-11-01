"""The README command table and the registry must agree."""
import pathlib
import re
import unittest

from textkit.cli import COMMANDS

ROOT = pathlib.Path(__file__).resolve().parent.parent


class DocsTest(unittest.TestCase):
    def test_readme_table_matches_registry(self):
        readme = (ROOT / "README.md").read_text()
        table = set(re.findall(r"^\| `([a-z]+)` \|", readme, flags=re.M))
        self.assertEqual(table, set(COMMANDS))
