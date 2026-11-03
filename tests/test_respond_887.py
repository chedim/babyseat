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

    def test_a_condition_said_of_nothing_is_checked_against_nothing(self):
        """Live 2026-11-03: "make the blocked/blocks parts a link" got "0 of 386 tool are blocks"."""
        srv, *_ = _world()
        r = LL.respond(srv, SID, "show the goals, the window is open")
        self.assertNotIn("goals are open", r)
        self.assertNotIn("of 2 goals", r)

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
        self.assertIn("membrane already said", ctx)
        self.assertIn("it ran nothing", ctx)

    def test_the_understanding_before_the_fill_is_what_the_shadow_keeps(self):
        srv, *_ = _world()
        self._ctx(srv, "is goal two shut?", reply="shut means closed")
        self.assertEqual(srv._cg_understood[SID]["surprise"], ["shut"])

    def test_nothing_understood_posts_nothing(self):
        srv, *_ = _world()
        ctx, posted, asked = self._ctx(srv, "yup", reply="yup: none")
        self.assertEqual(posted, [])


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class MemosReview(unittest.TestCase):
    """Memo's review of #892."""
    def test_a_hung_gap_call_never_holds_the_turn(self):
        import threading
        import time
        srv, *_ = _world()
        gate = threading.Event()
        old = LL._ask_model
        LL._ask_model = lambda prompt: gate.wait(5) and ("", {})
        try:
            t = time.monotonic()
            got = LL.fill_gaps_before(srv, SID, "zorblax goal one", ["zorblax"], wait_s=0.2)
            self.assertLess(time.monotonic() - t, 2.0)
            self.assertTrue(got["late"])
        finally:
            gate.set()
            LL._ask_model = old

    def test_a_condition_the_field_cant_evaluate_is_not_a_no(self):
        srv, f, est, *_ = _world()
        from membrane.entities import QuantumEntity
        est.upsert(QuantumEntity(eid="membrane::adjective:weird", cls=LL.CLASS_ADJECTIVE,   # refuses when run
                                 properties={"name": "weird", "filter": "x.status.deeper == 1"}), embedder=None)
        self.assertIn("can't tell whether goal one is weird", LL.respond(srv, SID, "is goal one weird?"))

    def test_the_answer_is_marked_as_now(self):
        srv, *_ = _world()
        self.assertTrue(LL.respond(srv, SID, "were the goals open yesterday?").startswith("Now: "))

    def test_a_group_is_called_by_its_noun(self):
        srv, *_ = _world()
        r = LL.respond(srv, SID, "how many goals are open?")
        self.assertIn("1 of 2 goals is open", r)

    def test_a_long_name_doesnt_slow_a_long_message(self):
        import time
        from membrane.entities import QuantumEntity
        srv, f, est, *_ = _world()
        est.upsert(QuantumEntity(eid="membrane::Component:long", cls="Component",
                                 properties={"name": " ".join(["word"] * 200)}), embedder=None)
        t = time.monotonic()
        LL.understand(srv, SID, " ".join(["word"] * 1500))
        self.assertLess(time.monotonic() - t, 5.0)

    def test_a_word_already_being_asked_is_not_asked_again(self):
        import threading
        srv, *_ = _world()
        gate, calls = threading.Event(), []
        old = LL._ask_model
        LL._ask_model = lambda prompt: calls.append(prompt) or gate.wait(5) and ("", {})
        try:
            for _ in range(3):
                LL.fill_gaps_before(srv, SID, "quuxify goal one", ["quuxify"], wait_s=0.2)
            self.assertEqual(len(calls), 1)
            got = LL.fill_gaps(srv, SID, "quuxify it", ["quuxify"])
            self.assertEqual(got["pending"], ["quuxify"])
        finally:
            gate.set()
            LL._ask_model = old

    def test_the_switch_stops_stage_3s_posts_too(self):
        from test_stage3_membrane_answers import _read_only_check
        srv, f, est, goal, *_ = _world()
        _read_only_check(srv, f, est, goal)
        os.environ["MEMBRANE_ANSWER"] = "0"
        try:
            ctx, posted, asked = BeforeTheTurn()._ctx(srv, "check goal one")
        finally:
            del os.environ["MEMBRANE_ANSWER"]
        self.assertEqual(posted, [])

    def test_the_switch_stops_the_posts(self):
        srv, *_ = _world()
        os.environ["MEMBRANE_ANSWER"] = "0"
        try:
            ctx, posted, asked = BeforeTheTurn()._ctx(srv, "goal two open?")
        finally:
            del os.environ["MEMBRANE_ANSWER"]
        self.assertEqual(posted, [])

    def test_the_shadow_keeps_the_before_score_and_the_fill(self):
        from test_conversation_graph import _seat
        from membrane import convgraph as CG
        from membrane import lanes as L
        srv, runner = _seat()
        LL.seed(srv.entity_store)
        runner.post_line = lambda sid, text: None
        srv._cg_turn, srv._cg_text = {SID: {}}, {SID: "frobnicate the goals"}
        old = LL._ask_model
        LL._ask_model = lambda prompt: ("frobnicate: none", {"output_tokens": 2})
        try:
            L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
            u = runner.messages.add(SID, "msg_u1", "user", "frobnicate the goals")
            a = runner.messages.add(SID, "msg_a1", "assistant", "done")
            CG.on_turn_end(srv, runner, SID, u, "frobnicate the goals", a, "done")
        finally:
            LL._ask_model = old
        sh = srv.entity_store.docs[CG.msg_eid("msg_u1")].properties["shadow"]
        self.assertIn("frobnicate", sh["understanding"]["surprise"])
        self.assertEqual(sh["filled"]["usage"], {"output_tokens": 2})

    def test_a_dead_turns_leftovers_dont_reach_the_next_message(self):
        srv, *_ = _world()
        srv._cg_filled = {SID: {"words": [{"word": "stale"}]}}
        BeforeTheTurn()._ctx(srv, "goal two open?")
        self.assertNotIn(SID, srv._cg_filled)


if __name__ == "__main__":
    unittest.main()
