"""GENERATION (the operator, 2026-11-01: "can the same mechanism be used in reverse to generate
responses?" -- yes, with lemminflect approved for verb forms).

Pinned here:
  - verbs inflect (closed, ran, filed, turned on), with plain rules when lemminflect is missing;
  - names join as English ("a", "a and b", "a, b and c");
  - a run of English ends in a sentence membrane words itself from its calls and results:
    "Closed goal one.", "Closed 2 things: goal one and goal two.", "Couldn't close goal two: ...";
  - what didn't map is worded too: an unknown verb, an object that names nothing, a verb with no
    meaning on that object, a method that needs more arguments, a write on many things.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import generate as GEN  # noqa: E402
from test_living_language import HAVE, _world  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import language as LL  # noqa: E402
from membrane import night as N  # noqa: E402


class Words(unittest.TestCase):
    def test_past_forms(self):
        for v, p in (("close", "closed"), ("run", "ran"), ("file", "filed"), ("turn on", "turned on"),
                     ("check", "checked")):
            self.assertEqual(GEN.past(v), p, v)

    def test_plain_rules_without_lemminflect(self):
        self.assertEqual(GEN._rule_past("close"), "closed")
        self.assertEqual(GEN._rule_past("stop"), "stopped")
        self.assertEqual(GEN._rule_past("try"), "tried")
        self.assertEqual(GEN._rule_past("merge"), "merged")

    def test_names_join(self):
        self.assertEqual(GEN.join(["a"]), "a")
        self.assertEqual(GEN.join(["a", "b"]), "a and b")
        self.assertEqual(GEN.join(["a", "b", "c"]), "a, b and c")


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Reports(unittest.TestCase):
    def test_one_thing_done(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        out = LG.run(srv, code="close the open goals", lang="en")
        self.assertTrue(out["output"].startswith("Closed goal one."), out["output"])

    def test_many_things_done(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        g2.properties["status"] = "open"
        out = LG.run(srv, code="close all the open goals", lang="en")
        self.assertTrue(out["output"].startswith("Closed 2 things: goal one and goal two."), out["output"])

    def test_a_failure_is_worded(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        N.method_define(srv, est, f, None, goal.eid, "close", "echo nope >&2; exit 3")
        out = LG.run(srv, code="close goal one", lang="en")
        self.assertTrue(out["output"].startswith("Couldn't close goal one"), out["output"])

    def test_what_didnt_map_is_worded(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        self.assertIn("I don't know how to frobnicate", LG.run(srv, code="frobnicate the goals", lang="en")["output"])
        self.assertIn("I don't know what", LG.run(srv, code="close the bogus widgets", lang="en")["output"])
        g2.properties["status"] = "open"
        self.assertIn("That would close 2 things", LG.run(srv, code="close the open goals", lang="en")["output"])


class MemosReviewOf820(unittest.TestCase):
    """Memo on #820 (2026-11-01): the reason is the last line, a silent failure says its exit
    code, no doubled full stop, long names are cut, an object starting with the verb's word is
    not misread, refusals are `said` too."""

    def test_the_reason_is_the_last_line(self):
        out = GEN.done("close", ["goal one"], [("goal one", "Traceback (most recent call last):\n  File x\nValueError: no such goal")])
        self.assertEqual(out, "Couldn't close goal one: ValueError: no such goal.")

    def test_a_silent_failure_says_its_exit_code(self):
        self.assertEqual(GEN.done("close", ["goal one"], [("goal one", "", 3)]), "Couldn't close goal one (exit 3).")

    def test_no_doubled_full_stop(self):
        self.assertEqual(GEN.done("close", ["g"], [("g", "goal is already closed.")]), "Couldn't close g: goal is already closed.")

    def test_long_names_are_cut(self):
        name = "a very long task description " * 10
        self.assertLess(len(GEN.done("close", [name], [])), 140)

    def test_an_object_starting_with_the_verbs_word(self):
        self.assertEqual(GEN.not_understood("close", ["close requests"]), 'I don\'t know what "close requests" is.')
        self.assertEqual(GEN.not_understood("close", [GEN.no_meaning("close", "goal one")]),
                         "I can't close goal one: nothing gives it that meaning.")

    @unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed")
    def test_refusals_are_said(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        out = LG.run(srv, code="frobnicate the goals", lang="en")
        self.assertIn("frobnicate", out.get("said") or "")


if __name__ == "__main__":
    unittest.main()
