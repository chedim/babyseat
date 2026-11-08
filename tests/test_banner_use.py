"""#934: which banner lines a turn uses, per rule (measure only; nothing is dropped).

A line is USED when the turn's reply carries what the line is about (the entity a rule bound,
or, for a recall line, a word only that line carries); a line shown and not so carried is
SHOWN-ONLY. Counts sit on the rule entity in hourly buckets (like score), so they survive a
restart.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_graph_banner import SID, Seeded  # noqa: E402
from test_pronoun_registers import _say  # noqa: E402
from membrane import banneruse as BU  # noqa: E402
from membrane import graphbanner as GB  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402


class Use(Seeded):
    def _named_beta(self):
        est = self.srv.entity_store
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:beta", cls="Goal",
                                 properties={"name": "beta", "status": "open"}))
        _say(self.srv, self.runner, 1, "look", "x")
        R.upsert(self.srv.efield.b, self.srv.efield.origin, CG.msg_eid("msg_a1"), "refers-to",
                 MEMBRANE_NS + "goal:beta", "chat")
        self.assertIn("named: beta (open)", GB.block(self.srv, SID))

    def _named(self):
        return self.srv.entity_store.docs[GB._rule_eid("named")]

    def test_a_turn_using_a_named_line_counts_a_use(self):
        self._named_beta()
        BU.settle(self.srv, SID, "beta is still open, so I will carry on with it.")
        c = BU.counts(self._named())
        self.assertEqual((c["shown"], c["used"]), (1, 1))

    def test_a_line_shown_and_unused_counts_shown_only(self):
        self._named_beta()
        BU.settle(self.srv, SID, "Nothing to add on that.")
        c = BU.counts(self._named())
        self.assertEqual((c["shown"], c["used"]), (1, 0))

    def test_the_turn_end_settles_what_the_banner_showed(self):
        self._named_beta()
        _say(self.srv, self.runner, 2, "status?", "beta is open.")
        c = BU.counts(self._named())
        self.assertEqual((c["shown"], c["used"]), (1, 1))
        self.assertFalse(getattr(self.srv, "_banner_shown", {}).get(SID))   # settled once

    def test_counts_survive_a_restart(self):
        self._named_beta()
        BU.settle(self.srv, SID, "beta again")
        ent = self._named()
        again = QuantumEntity(eid=ent.eid, cls=ent.cls, properties=json.loads(json.dumps(ent.properties)))
        c = BU.counts(again)
        self.assertEqual((c["shown"], c["used"]), (1, 1))
        self.assertTrue(any(k.isdigit() for k in again.properties["use_hours"]))   # hourly buckets

    def test_the_rules_listing_shows_each_use_rate(self):
        self._named_beta()
        BU.settle(self.srv, SID, "beta")
        row = next(r for r in GB.listing(self.srv.entity_store) if r["name"] == "named")
        self.assertEqual((row["shown"], row["used"]), (1, 1))
        self.assertEqual(row["use_rate"], 1.0)

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

    def test_the_recall_entity_is_listed_as_recall_not_retired(self):
        BU.shown_recall(self.srv, SID, ["the zebra plan"])
        BU.settle(self.srv, SID, "zebra")
        ent = self.srv.entity_store.docs[BU.RECALL_EID]
        self.assertFalse((ent.properties or {}).get("retired"))
        self.assertNotIn(ent, GB._rules(self.srv.entity_store))
        row = next(r for r in GB.listing(self.srv.entity_store) if r["eid"] == BU.RECALL_EID)
        self.assertEqual(row["kind"], "recall")

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
