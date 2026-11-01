"""QUESTIONS COMPILE TO QUERIES (the operator, 2026-11-01: "then questions as queries"; the first
step of Blurp's `invent`): a question is a read-only query over the field, answered in words
by membrane -- and an answer says what the field HAS, never that nothing exists.

  which / what <adjectives> <noun>s ...?  -> members of the class narrowed by its adjectives
  how many <adjectives> <noun>s?          -> a count
  is <entity> <adjective>?                -> yes or no, with the entity's own status
  what does <word> mean?                  -> what the word was taught to mean
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


def _ask(srv, est, q):
    LL.seed_executor(est)
    return LG.run(srv, code=q, lang="en")


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Questions(unittest.TestCase):
    def test_which_members(self):
        srv, f, est, goal, g1, g2 = _world()
        g2.properties["status"] = "open"
        out = _ask(srv, est, "which open goals are there?")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["said"], "2 open goals: goal one and goal two.")

    def test_none_is_what_the_field_has(self):
        srv, f, est, goal, g1, g2 = _world()
        out = _ask(srv, est, "what blocked goals are there?")
        self.assertEqual(out["said"], "The field has no blocked goals.")

    def test_how_many(self):
        srv, f, est, goal, g1, g2 = _world()
        self.assertEqual(_ask(srv, est, "how many open goals?")["said"], "1 open goal.")

    def test_is_it(self):
        srv, f, est, goal, g1, g2 = _world()
        self.assertEqual(_ask(srv, est, "is goal one open?")["said"], "Yes, goal one is open.")
        self.assertEqual(_ask(srv, est, "is goal two open?")["said"], "No: goal two is closed.")

    def test_what_a_word_means(self):
        srv, f, est, goal, g1, g2 = _world()
        _ask(srv, est, "finish means close")
        self.assertEqual(_ask(srv, est, "what does finish mean?")["said"], '"finish" means close.')

    def test_a_question_never_writes(self):
        srv, f, est, goal, g1, g2 = _world()
        [step] = LL.compile(srv, SID, "which open goals are there?")
        self.assertEqual(step["kind"], "query")
        self.assertFalse(step["writes"])

    def test_a_question_it_cant_read_is_said(self):
        srv, f, est, goal, g1, g2 = _world()
        out = _ask(srv, est, "why are goals slow?")
        self.assertFalse(out["ok"])
        self.assertIn("I can't answer that from the field", out["said"])

    def test_a_chat_question_brings_the_fields_answer(self):
        from membrane import lanes as L
        srv, f, est, goal, g1, g2 = _world()
        srv._cg_turn = {SID: {}}
        srv._cg_text = {SID: "which open goals are there?"}
        ctx = L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
        self.assertIn("The field answers the person's question: 1 open goal: goal one.", ctx)


if __name__ == "__main__":
    unittest.main()
