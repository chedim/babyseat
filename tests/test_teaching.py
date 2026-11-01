"""TEACHING WORDS IN PLAIN LANGUAGE (the operator, 2026-11-01: "lets do the teaching"). A
definition is a sentence ("X means Y"); it compiles to a define step and teaches the field:

  a known verb        -> a synonym ("finish means close": "finish goal one" closes it)
  a known adjective   -> the same filter ("finished means closed")
  not Y / Y and Z     -> a combined filter
  a property, a value -> a filter ("stuck means status blocked")
  an entity's name    -> another name for it ("my boat means goal one")

What can't be made into one of those is said, nothing guessed; membrane words what it learned.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_living_language import HAVE, SID, _world  # noqa: E402
from membrane import lang as LG  # noqa: E402
from membrane import language as LL  # noqa: E402


def _teach(srv, est, text):
    LL.seed_executor(est)
    return LG.run(srv, code=text, lang="en")


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Teaching(unittest.TestCase):
    def test_a_verb_synonym(self):
        srv, f, est, goal, g1, g2 = _world()
        out = _teach(srv, est, "finish means close")
        self.assertTrue(out["ok"], out)
        self.assertIn('Got it: "finish" now means close', out["output"])
        [step] = LL.compile(srv, SID, "finish goal one")
        self.assertEqual([c["method"] for c in step["calls"]], ["close"])

    def test_a_taught_synonym_survives_boot(self):
        from membrane import verbs as V
        srv, f, est, goal, g1, g2 = _world()
        _teach(srv, est, "finish means close")
        V.boot(srv)
        self.assertIn(V.verb_eid("finish"), est.docs)

    def test_an_adjective_synonym(self):
        srv, f, est, goal, g1, g2 = _world()
        _teach(srv, est, "finished means closed")
        [step] = LL.compile(srv, SID, "close the finished goals")
        self.assertEqual(step["targets"], [g2.eid])

    def test_not_and_and(self):
        srv, f, est, goal, g1, g2 = _world()
        _teach(srv, est, "pending means not closed")
        [step] = LL.compile(srv, SID, "close the pending goals")
        self.assertEqual(step["targets"], [g1.eid])
        g1.properties["owner"] = "me"
        _teach(srv, est, "mine means owner me")
        _teach(srv, est, "urgent means pending and mine")
        [step] = LL.compile(srv, SID, "close the urgent goals")
        self.assertEqual(step["targets"], [g1.eid])

    def test_a_property_and_value(self):
        srv, f, est, goal, g1, g2 = _world()
        g2.properties["status"] = "blocked"
        _teach(srv, est, "stuck means status blocked")
        [step] = LL.compile(srv, SID, "close the stuck goals")
        self.assertEqual(step["targets"], [g2.eid])

    def test_another_name_for_an_entity(self):
        srv, f, est, goal, g1, g2 = _world()
        _teach(srv, est, "my boat means goal one")
        [step] = LL.compile(srv, SID, "close my boat")
        self.assertEqual(step["targets"], [g1.eid])

    def test_what_cant_be_defined_is_said(self):
        srv, f, est, goal, g1, g2 = _world()
        out = _teach(srv, est, "stale means not updated for a day")
        self.assertFalse(out["ok"])
        self.assertIn("I don't know how to define \"stale\" that way yet", out["output"])

    def test_a_chat_definition_is_told_to_the_model(self):
        from membrane import lanes as L
        srv, f, est, goal, g1, g2 = _world()
        srv._cg_turn = {SID: {}}
        srv._cg_text = {SID: "finish means close"}
        ctx = L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
        self.assertIn('teaches "finish" = close', ctx)


if __name__ == "__main__":
    unittest.main()
