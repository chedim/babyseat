"""#934: which banner lines a turn uses (measure only; nothing is dropped).

A recall line is USED when the turn's reply carries a word only that line carries; a line shown and
not so carried is SHOWN-ONLY. Counts sit on the recall entity in hourly buckets (like score), so they
survive a restart.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_conversation_graph import SID, _seat  # noqa: E402
from membrane import banneruse as BU  # noqa: E402
from membrane.entities import QuantumEntity  # noqa: E402


class Use(unittest.TestCase):
    def setUp(self):
        self.srv, self.runner = _seat()

    def test_counts_survive_a_restart(self):
        BU.shown_recall(self.srv, SID, ["the zebra plan"])
        BU.settle(self.srv, SID, "zebra again")
        ent = self.srv.entity_store.docs[BU.RECALL_EID]
        again = QuantumEntity(eid=ent.eid, cls=ent.cls, properties=json.loads(json.dumps(ent.properties)))
        c = BU.counts(again)
        self.assertEqual((c["shown"], c["used"]), (1, 1))
        self.assertTrue(any(k.isdigit() for k in again.properties["use_hours"]))   # hourly buckets

    def test_recall_lines_are_counted_on_the_recall_rule(self):
        BU.shown_recall(self.srv, SID, ["the zebra crossing plan #12", "the quokka budget #13"])
        BU.settle(self.srv, SID, "About the quokka: it is fine.")
        c = BU.counts(self.srv.entity_store.docs[BU.RECALL_EID])
        self.assertEqual((c["shown"], c["used"]), (2, 1))

    def test_the_recall_prefix_is_not_content(self):
        line = "\u25b8 #12 (3 min ago, earlier answer) the zebra crossing plan"
        BU.shown_recall(self.srv, SID, [line])
        BU.settle(self.srv, SID, "Earlier answer, a while ago: nothing to add.")
        c = BU.counts(self.srv.entity_store.docs[BU.RECALL_EID])
        self.assertEqual((c["shown"], c["used"]), (1, 0))

    def test_concurrent_adds_lose_nothing(self):
        import threading
        BU.shown_recall(self.srv, SID, ["x"])
        BU.settle(self.srv, SID, "")
        ts = [threading.Thread(target=lambda: [BU._add(self.srv, BU.RECALL_EID, 1, 1, 5000.0) for _ in range(50)]) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        c = BU.counts(self.srv.entity_store.docs[BU.RECALL_EID])
        self.assertEqual(c["shown"], 1 + 200)


if __name__ == "__main__":
    unittest.main()
