"""COMPILE-FIRST ROUTING, STAGE 3: THE MODEL FILLS THE GAPS (the operator, 2026-11-02: "build
it"). A person's message that compiles except for words the field doesn't know is told to the
model with those words, typed, and the existing teaching sentence that fills each one
("X means Y", docs/cliffhanger.md): an unknown verb means a known verb, an unknown object
another name for an entity. Taught once, the same message compiles the next time. Gaps no
definition can fill (no object, a verb with no meaning on that thing) are not told.
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


def _ctx(srv, said):
    from membrane import lanes as L
    srv._cg_turn = {SID: {}}
    srv._cg_text = {SID: said}
    return L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class GapsToTheModel(unittest.TestCase):
    def test_an_unknown_verb_is_told_with_its_teaching_sentence(self):
        srv, f, est, goal, g1, g2 = _world()
        ctx = _ctx(srv, "shut goal one")
        self.assertIn('"shut"', ctx)
        self.assertIn("shut means <a verb it knows>", ctx)
        self.assertIn("exec(action='run', lang='en'", ctx)

    def test_an_unknown_object_is_told_with_its_teaching_sentence(self):
        srv, f, est, goal, g1, g2 = _world()
        ctx = _ctx(srv, "close the zebra")
        self.assertIn('"zebra"', ctx)
        self.assertIn("zebra means <the name of a thing it knows>", ctx)

    def test_once_taught_the_message_compiles_and_no_gap_is_told(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.seed_executor(est)
        self.assertTrue(LG.run(srv, code="shut means close", lang="en")["ok"])
        ctx = _ctx(srv, "shut goal one")
        self.assertNotIn("doesn't know", ctx)
        self.assertIn("compiles to:", ctx)

    def test_the_object_word_told_is_the_one_that_teaches(self):
        srv, f, est, goal, g1, g2 = _world()
        [g] = LL.teachable(LL.compile(srv, SID, "close the zebra"))
        LL.seed_executor(est)
        self.assertTrue(LG.run(srv, code=f"{g['word']} means goal one", lang="en")["ok"])
        [step] = LL.compile(srv, SID, "close the zebra")
        self.assertTrue(step["mapped"], step)

    def test_a_gap_no_definition_fills_is_not_told(self):
        srv, f, est, goal, g1, g2 = _world()
        ctx = _ctx(srv, "close")
        self.assertEqual(ctx, "BANNER")

    def test_a_message_with_no_command_gets_nothing(self):
        srv, f, est, goal, g1, g2 = _world()
        self.assertEqual(_ctx(srv, "thanks, that looks good"), "BANNER")

    def test_classify_and_the_turn_read_the_same_gaps(self):
        srv, f, est, goal, g1, g2 = _world()
        steps = LL.compile(srv, SID, "shut the zebra")
        self.assertEqual(LL.gaps(steps), LL.classify(srv, SID, "shut the zebra")["gaps"])


if __name__ == "__main__":
    unittest.main()
