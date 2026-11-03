"""COMPILE-FIRST, BUILD C (#887; the operator, 2026-11-03: "membrane receives a message, tries to
compile it, uses llm to fix compiling gaps and then, with fixed gaps, that message graph can be
used to generate response" -- "that is the idea").

Before the model's turn, membrane fills the message's surprise words (build B, now before the
turn), then words a reply from the filled graph itself: what each condition says about each thing
the message names, a read-only program's result, and what it still doesn't know. It is posted
marked "🧩 membrane (no model):"; the model still answers. No sentence types.
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
from membrane import language as LL  # noqa: E402

MARK = "🧩 membrane (no model):"


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Respond(unittest.TestCase):
    def test_a_condition_on_a_thing_is_checked_against_the_field(self):
        srv, *_ = _world()
        self.assertIn("goal one is open", LL.respond(srv, SID, "is goal one open?"))
        self.assertIn("goal two isn't open", LL.respond(srv, SID, "goal two open?"))

    def test_a_thing_alone_says_its_state(self):
        srv, *_ = _world()
        self.assertIn("goal two: closed", LL.respond(srv, SID, "what about goal two"))

    def test_what_it_still_doesnt_know_is_said(self):
        srv, *_ = _world()
        r = LL.respond(srv, SID, "frobnicate goal one")
        self.assertIn("goal one: open", r)
        self.assertIn("frobnicate", r)

    def test_nothing_understood_says_nothing(self):
        srv, *_ = _world()
        self.assertEqual(LL.respond(srv, SID, "yup"), "")

    def test_a_word_taught_as_a_condition_counts_whatever_its_tag(self):
        srv, f, est, *_ = _world()
        [step] = LL.compile(srv, SID, "landed means status closed")
        LL.teach(est, step)
        self.assertEqual(LL.understand(srv, SID, "goal two landed")["surprise"], [])
        self.assertIn("goal two is landed", LL.respond(srv, SID, "goal two landed"))

    def test_a_write_is_never_run(self):
        srv, f, est, goal, g1, g2 = _world()
        LL.respond(srv, SID, "close goal one")
        self.assertEqual(est.docs[g1.eid].properties.get("status"), "open")


class _Runner:
    def __init__(self):
        self.posted = []
        self.sessions = None

    def post_line(self, sid, text):
        self.posted.append((sid, text))


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class BeforeTheTurn(unittest.TestCase):
    def _ctx(self, srv, said, reply="frobnicate: none"):
        from membrane import lanes as L
        srv._loop_runner = _Runner()
        srv._cg_turn = {SID: {}}
        srv._cg_text = {SID: said}
        asked = []
        old = LL._ask_model
        LL._ask_model = lambda prompt: asked.append(prompt) or (reply, {})
        try:
            ctx = L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
        finally:
            LL._ask_model = old
        return ctx, srv._loop_runner.posted, asked

    def test_the_gaps_are_filled_then_membrane_answers_marked(self):
        srv, *_ = _world()
        ctx, posted, asked = self._ctx(srv, "is goal two shut?", reply="shut means closed")
        self.assertEqual(len(asked), 1)
        [(sid, text)] = posted
        self.assertTrue(text.startswith(MARK), text)
        self.assertIn("goal two is shut", text)
        self.assertIn("membrane already answered", ctx)

    def test_the_understanding_before_the_fill_is_what_the_shadow_keeps(self):
        srv, *_ = _world()
        self._ctx(srv, "is goal two shut?", reply="shut means closed")
        self.assertEqual(srv._cg_understood[SID]["surprise"], ["shut"])

    def test_nothing_understood_posts_nothing(self):
        srv, *_ = _world()
        ctx, posted, asked = self._ctx(srv, "yup", reply="yup: none")
        self.assertEqual(posted, [])


if __name__ == "__main__":
    unittest.main()
