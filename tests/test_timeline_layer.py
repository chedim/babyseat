"""The timeline layer (Phase T): the fn schema extraction (the typed
transition layer) + the token transition table (the token-grain timeline).
Each test verifies an assumption, per the E-discipline."""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.fn_schema import extract_fn, extract_fn_from_exemplar
from membrane.token_timeline import TokenTimeline, tokenize, context_key


class TestFnExtraction(unittest.TestCase):
    """The fn schema's extraction: the typed transition layer on
    actuation cells."""

    def test_edit_call_parsed(self):
        fn = extract_fn_from_exemplar(
            'tool edit for: edit the file\n'
            'args: {"filePath": "/tmp/x.py", "oldString": "a"}',
            "Edit applied successfully.")
        self.assertIsNotNone(fn)
        self.assertEqual(fn["op"], "edit")
        self.assertEqual(fn["binds"]["filePath"], "/tmp/x.py")
        self.assertEqual(fn["returns"], "Edit applied successfully.")

    def test_bash_call_parsed(self):
        fn = extract_fn_from_exemplar(
            "tool bash for: run the tests\nargs: {}",
            "3 passed, 0 failed")
        self.assertIsNotNone(fn)
        self.assertEqual(fn["op"], "bash")

    def test_prose_returns_none(self):
        """The degrade covenant: prose cells stay untyped (the two-axis
        design -- the fn schema covers actuations only)."""
        fn = extract_fn_from_exemplar(
            "why does consensus exist? because a single vote misfired",
            "the rationale recorded")
        self.assertIsNone(fn)

    def test_extract_fn_direct(self):
        """The direct parse from (name, args, result) -- the receipt lane's
        classifier shape."""
        fn = extract_fn("edit", '{"filePath": "/tmp/x.py"}',
                        "Edit applied successfully.")
        self.assertEqual(fn["op"], "edit")
        self.assertEqual(fn["binds"]["filePath"], "/tmp/x.py")

    def test_unknown_tool_returns_none(self):
        fn = extract_fn("mystery_tool", '{"x": 1}', "whatever")
        self.assertIsNone(fn)


class TestTokenTimeline(unittest.TestCase):
    """The token transition table: the timeline at token grain -- every
    (context → next-token) pair the chat stream produces, durably stored,
    incrementally fed."""

    def setUp(self):
        self.tt = TokenTimeline()
        self.stream = tokenize(
            "the gate escalates when the sim is below the bar")
        self.tt.observe(self.stream)

    def test_round_trip(self):
        """A lived context produces a distribution; the walk reproduces
        the lived path (single-observation: each edge has count 1, so
        the walk follows the only known path)."""
        dist = self.tt.distribution(["the", "gate"])
        self.assertIsNotNone(dist)
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=5)
        gen = self.tt.walk(["the", "gate"])
        self.assertIsNotNone(gen)
        self.assertGreater(len(gen), 2)

    def test_unlived_context_returns_none(self):
        """The honest miss: an uncovered context returns None (the null
        lane at token grain -- the walk refuses, it doesn't drift)."""
        dist = self.tt.distribution(["quantum", "teleportation"])
        self.assertIsNone(dist)

    def test_backoff_ladder(self):
        """The K ladder falls through: K=3 misses on a 2-token context ->
        K=2 hits (the `break` -> `continue` fix)."""
        dist = self.tt.distribution(["the", "gate"])
        self.assertIsNotNone(dist)   # only 2 tokens: K=3 can't match, K=2 does

    def test_incremental_growth(self):
        """The table grows incrementally: observe a second stream, the
        new transitions are added without rebuilding. The same context
        prefix ('the gate') in both streams reinforces the shared next
        token ('escalates' x2)."""
        more = tokenize("the gate escalates when the field is locked")
        self.tt.observe(more)
        key = context_key(["the", "gate"], 2)
        dist = self.tt._local.get(key)
        self.assertIsNotNone(dist)
        self.assertEqual(dist.get("escalates"), 2,
                         f"shared prefix not reinforced: {dist}")


if __name__ == "__main__":
    unittest.main()
