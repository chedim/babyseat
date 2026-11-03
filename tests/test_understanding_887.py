"""COMPILE-FIRST, AS MEANT (#887; the operator, 2026-11-03: "the idea here is trying to use
compilability of language in the message as understanding metric, like news/surprise").

Every meaning node of a message's tree (a noun phrase, or an open-class word outside one) tries
to map to something the field already means. Understanding is the share that maps; the rest is
surprise, what the model would be asked to fill. No sentence types: a reply, a report and a
question are scored the same way.
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
class Understanding(unittest.TestCase):
    def test_a_message_the_field_knows_is_understood_whole(self):
        srv, *_ = _world()
        u = LL.understand(srv, SID, "close the open goals")
        self.assertEqual(u["share"], 1.0)
        self.assertEqual(u["surprise"], [])

    def test_what_doesnt_map_is_the_surprise(self):
        srv, *_ = _world()
        u = LL.understand(srv, SID, "frobnicate the goals")
        self.assertEqual(u["surprise"], ["frobnicate"])
        self.assertEqual((u["mapped"], u["nodes"]), (1, 2))
        self.assertEqual(u["share"], 0.5)

    def test_a_reply_is_scored_not_dropped(self):
        srv, *_ = _world()
        u = LL.understand(srv, SID, "yup")
        self.assertEqual((u["share"], u["surprise"]), (0.0, ["yup"]))

    def test_grammar_words_are_not_meaning_nodes(self):
        srv, *_ = _world()
        u = LL.understand(srv, SID, "close the goals -- and the open goals.")
        self.assertEqual(u["surprise"], [])

    def test_a_taught_word_stops_being_surprise(self):
        srv, f, est, *_ = _world()
        self.assertEqual(LL.understand(srv, SID, "finish the goals")["surprise"], ["finish"])
        [step] = LL.compile(srv, SID, "finish means close")
        LL.teach(est, step)
        self.assertEqual(LL.understand(srv, SID, "finish the goals")["share"], 1.0)

    def test_nothing_to_score_is_none(self):
        srv, *_ = _world()
        self.assertIsNone(LL.understand(srv, SID, "?!")["share"])

    def test_the_report_sums_shares_and_surprise(self):
        rows = [{"outcome": "gaps", "understanding": {"nodes": 2, "mapped": 1, "share": 0.5,
                                                      "surprise": ["landed"]}},
                {"outcome": "thinking", "understanding": {"nodes": 1, "mapped": 0, "share": 0.0,
                                                          "surprise": ["landed"]}},
                {"outcome": "thinking"}]
        r = LL.shadow_report(rows)["understanding"]
        self.assertEqual(r["scored"], 2)
        self.assertEqual(r["mean_share"], 0.25)
        self.assertEqual(r["surprise_top"][0], ["landed", 2])


@unittest.skipUnless(HAVE, "spaCy and en_core_web_sm not installed (pip install babyseat[nlp])")
class Recorded(unittest.TestCase):
    def test_the_turn_keeps_its_understanding(self):
        from test_conversation_graph import _seat
        srv, runner = _seat()
        LL.seed(srv.entity_store)
        u = runner.messages.add(SID, "msg_u1", "user", "frobnicate the goals")
        a = runner.messages.add(SID, "msg_a1", "assistant", "done")
        CG.on_turn_end(srv, runner, SID, u, "frobnicate the goals", a, "done")
        sh = srv.entity_store.docs[CG.msg_eid("msg_u1")].properties["shadow"]
        self.assertIn("frobnicate", sh["understanding"]["surprise"])
        self.assertEqual(sh["outcome"], "gaps")          # stage 1-3's reading stays beside it


if __name__ == "__main__":
    unittest.main()
