import unittest

from textkit import core


class CoreTest(unittest.TestCase):
    def test_shout(self):
        self.assertEqual(core.shout("hi there"), "HI THERE")

    def test_count_words(self):
        self.assertEqual(core.count_words("  one two\tthree\n"), 3)
