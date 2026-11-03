"""COMPILE-FIRST STAGE 3, THE OTHER HALF: MEMBRANE ANSWERS A READ-ONLY MESSAGE ITSELF (the
operator, 2026-11-02: "build it"; "I want membrane results clearly marked in the chat so that I
can evaluate them"). A person's message that compiles fully to calls of read-only methods is run
by membrane before the model's turn, and its result is posted in the chat marked
"🧩 membrane (no model):". The model still answers: it is told what membrane said, not to run
it again, and to add or correct. A message that writes anything is never run by membrane.
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
from membrane import night as N  # noqa: E402

MARK = "🧩 membrane (no model):"


class _Runner:
    def __init__(self):
        self.posted = []
        self.sessions = None

    def post_line(self, sid, text):
        self.posted.append((sid, text))


def _ctx(srv, said):
    from membrane import lanes as L
    srv._loop_runner = _Runner()
    srv._cg_turn = {SID: {}}
    srv._cg_text = {SID: said}
    return L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False), srv._loop_runner.posted


def _read_only_check(srv, f, est, goal):
    out = N.method_define(srv, est, f, None, goal.eid, "check", "echo {{self.name}} is fine", read_only=True)
    assert out.get("ok"), out


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class MembraneAnswers(unittest.TestCase):
    def test_a_read_only_message_is_answered_by_membrane_marked(self):
        srv, f, est, goal, g1, g2 = _world()
        _read_only_check(srv, f, est, goal)
        ctx, posted = _ctx(srv, "check goal one")
        self.assertEqual(len(posted), 1, posted)
        sid, text = posted[0]
        self.assertEqual(sid, SID)
        self.assertTrue(text.startswith(MARK), text)
        self.assertIn("goal one is fine", text)

    def test_the_model_is_told_membrane_answered_and_not_to_run_it_again(self):
        srv, f, est, goal, g1, g2 = _world()
        _read_only_check(srv, f, est, goal)
        ctx, _ = _ctx(srv, "check goal one")
        self.assertIn("membrane already ran", ctx)
        self.assertIn("don't run it again", ctx.lower())
        self.assertNotIn("exec(action='run', lang='en', code='check goal one') runs it", ctx)

    def test_a_message_that_writes_is_never_run_by_membrane(self):
        srv, f, est, goal, g1, g2 = _world()               # close is a write method
        ctx, posted = _ctx(srv, "close goal one")
        self.assertEqual(est.docs[g1.eid].properties.get("status"), "open")   # never run
        self.assertEqual([t for _, t in posted], [MARK + " Now: goal one: open"])   # #887 C: what it knows
        self.assertIn("compiles to:", ctx)
        self.assertIn("it ran nothing", ctx)                       # Memo on #892: never "don't run it again"
        self.assertNotIn("don't run it again", ctx.lower())

    def test_a_message_with_gaps_is_not_run(self):
        srv, f, est, goal, g1, g2 = _world()
        _read_only_check(srv, f, est, goal)
        ctx, posted = _ctx(srv, "check the zebra")
        self.assertEqual(posted, [])


if __name__ == "__main__":
    unittest.main()
