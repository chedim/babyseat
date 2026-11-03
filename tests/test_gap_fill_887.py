"""COMPILE-FIRST, BUILD B (#887; the operator, 2026-11-03: "whenever membrane identifies a gap in
meaning (by not being able to compile), it goes to llm and asks to fit that gap"; Haiku, and a
learned definition applies right away).

Membrane itself asks the model about a message's surprise words only (one small call, not the
chat turn), keeps each answer it can as a definition through the existing "X means Y" path, and
marks every word it asked so it is never asked again. The model here is a fake.
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


class Fake:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.reply, {"input_tokens": 10, "output_tokens": 5}


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class GapFill(unittest.TestCase):
    def test_a_kept_answer_is_understood_next_time(self):
        srv, f, est, *_ = _world()
        ask = Fake("finish means close")
        got = LL.fill_gaps(srv, SID, "finish the goals", ["finish"], ask=ask)
        self.assertEqual([(g["word"], g["kept"]) for g in got["words"]], [("finish", True)])
        self.assertIn("finish", ask.prompts[0])
        self.assertIn("close", ask.prompts[0])               # the verbs the field knows ride the question
        self.assertEqual(LL.understand(srv, SID, "finish the goals")["share"], 1.0)

    def test_a_word_is_asked_once(self):
        srv, *_ = _world()
        ask = Fake("yup: none")
        LL.fill_gaps(srv, SID, "yup", ["yup"], ask=ask)
        again = LL.fill_gaps(srv, SID, "yup, again", ["yup"], ask=ask)
        self.assertEqual(len(ask.prompts), 1)
        self.assertEqual(again["words"], [])

    def test_no_keepable_meaning_is_recorded_not_kept(self):
        srv, *_ = _world()
        got = LL.fill_gaps(srv, SID, "yup", ["yup"], ask=Fake("yup: none"))
        [w] = got["words"]
        self.assertEqual((w["word"], w["kept"]), ("yup", False))
        self.assertEqual(LL.understand(srv, SID, "yup")["surprise"], ["yup"])

    def test_an_answer_the_field_cant_keep_is_not_kept(self):
        srv, *_ = _world()
        got = LL.fill_gaps(srv, SID, "frobnicate it", ["frobnicate"], ask=Fake("frobnicate means zorp"))
        self.assertEqual(got["words"][0]["kept"], False)

    def test_one_call_for_all_of_a_messages_words(self):
        srv, *_ = _world()
        ask = Fake("finish means close\nyup: none")
        got = LL.fill_gaps(srv, SID, "yup, finish the goals", ["yup", "finish"], ask=ask)
        self.assertEqual(len(ask.prompts), 1)
        self.assertEqual(sorted(g["word"] for g in got["words"]), ["finish", "yup"])
        self.assertEqual(got["usage"], {"input_tokens": 10, "output_tokens": 5})

    def test_a_model_error_keeps_nothing_and_marks_nothing(self):
        srv, *_ = _world()

        def boom(prompt):
            raise RuntimeError("no key")
        got = LL.fill_gaps(srv, SID, "finish the goals", ["finish"], ask=boom)
        self.assertIn("no key", got["error"])
        ask = Fake("finish means close")
        LL.fill_gaps(srv, SID, "finish the goals", ["finish"], ask=ask)
        self.assertEqual(len(ask.prompts), 1)                # not marked asked: tried again

    def test_the_switch(self):
        srv, *_ = _world()
        ask = Fake("finish means close")
        os.environ["MEMBRANE_GAP_FILL"] = "0"
        try:
            self.assertIsNone(LL.fill_gaps(srv, SID, "finish the goals", ["finish"], ask=ask))
        finally:
            del os.environ["MEMBRANE_GAP_FILL"]
        self.assertEqual(ask.prompts, [])


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class TheTurn(unittest.TestCase):
    def _run(self, owner):
        from test_conversation_graph import _seat
        from membrane import convgraph as CG
        srv, runner = _seat()
        LL.seed(srv.entity_store)
        runner.sessions = type("S", (), {"get": lambda self, sid: {"id": sid, "owner": owner}})()
        asked = []
        old = (CG._spawn, LL._ask_model)
        CG._spawn = lambda fn: fn()
        LL._ask_model = lambda prompt: asked.append(prompt) or ("frobnicate: none", {"output_tokens": 3})
        try:
            u = runner.messages.add(SID, "msg_u1", "user", "frobnicate the goals")
            a = runner.messages.add(SID, "msg_a1", "assistant", "done")
            CG.on_turn_end(srv, runner, SID, u, "frobnicate the goals", a, "done")
        finally:
            CG._spawn, LL._ask_model = old
        return srv.entity_store.docs[CG.msg_eid("msg_u1")].properties["shadow"], asked

    def test_the_operators_turn_fills_its_surprise(self):
        sh, asked = self._run("owner")
        self.assertEqual(len(asked), 1)
        self.assertIn("frobnicate", [w["word"] for w in sh["filled"]["words"]])
        self.assertIn("understanding", sh)

    def test_a_guests_turn_teaches_nothing(self):
        sh, asked = self._run("some-guest-sub")
        self.assertEqual(asked, [])
        self.assertNotIn("filled", sh)


if __name__ == "__main__":
    unittest.main()
