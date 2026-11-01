"""COMPILE-FIRST ROUTING, STAGE 1: SHADOW (the operator, 2026-11-01: membrane compiles the
person's message, closes gaps with narrow model turns, answers itself; "ok, do shadow").

Shadow changes nothing and calls no model: every person message in a chat is compiled
locally and the outcome is kept on its message entity, next to what the model then did, so the
real rates exist before anything answers on its own.

  compiled   a command, a question or a definition, every part mapped
  gaps       typed: unknown_verb, no_meaning, needs_args, unknown_object, no_object
  thinking   nothing to compile ("thoughts on X?", "ok")
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
from membrane import convgraph as CG  # noqa: E402
from membrane import language as LL  # noqa: E402


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Classify(unittest.TestCase):
    def test_outcomes(self):
        srv, f, est, goal, g1, g2 = _world()
        self.assertEqual(LL.classify(srv, SID, "close goal one")["outcome"], "compiled")
        self.assertEqual(LL.classify(srv, SID, "which open goals are there?")["outcome"], "compiled")
        self.assertEqual(LL.classify(srv, SID, "finish means close")["outcome"], "compiled")
        self.assertEqual(LL.classify(srv, SID, "thoughts on the field design?")["outcome"], "thinking")
        self.assertEqual(LL.classify(srv, SID, "ok")["outcome"], "thinking")

    def test_gap_types(self):
        srv, f, est, goal, g1, g2 = _world()
        self.assertEqual([g["type"] for g in LL.classify(srv, SID, "frobnicate the goals")["gaps"]], ["unknown_verb"])
        self.assertEqual([g["type"] for g in LL.classify(srv, SID, "close the bogus widgets")["gaps"]],
                         ["unknown_object"])


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Recorded(unittest.TestCase):
    def test_the_turn_keeps_its_shadow_next_to_what_the_model_did(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_conversation_graph import _seat
        srv, runner = _seat()
        LL.seed(srv.entity_store)
        u = runner.messages.add(SID, "msg_u1", "user", "frobnicate the goals")
        a = runner.messages.add(SID, "msg_a1", "assistant", "done")
        runner.messages.rows[SID][-1]["parts"].append({"type": "tool", "tool": "exec"})
        CG.on_turn_end(srv, runner, SID, u, "frobnicate the goals", a, "done")
        sh = srv.entity_store.docs[CG.msg_eid("msg_u1")].properties["shadow"]
        self.assertEqual(sh["outcome"], "gaps")
        self.assertEqual(sh["model_tools"], ["exec"])

    def test_the_report_counts(self):
        rows = [{"outcome": "compiled", "gaps": [], "model_tools": []},
                {"outcome": "gaps", "gaps": [{"type": "unknown_verb", "what": "x"}], "model_tools": ["exec"]},
                {"outcome": "thinking", "gaps": [], "model_tools": []},
                {"outcome": "thinking", "gaps": [], "model_tools": ["exec"]}]
        r = LL.shadow_report(rows)
        self.assertEqual(r["total"], 4)
        self.assertEqual(r["outcomes"], {"compiled": 1, "gaps": 1, "thinking": 2})
        self.assertEqual(r["gap_types"], {"unknown_verb": 1})


if __name__ == "__main__":
    unittest.main()
