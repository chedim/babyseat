"""THE GRAPH-WALK LANE (the operator, 2026-11-01: measured first -- the walk brought something
the turn then used in 70% of turns, text recall 44% -- then "absolutely" to rules written as
cliffhanger patterns extended with the graph's edges; the walk looks back 8 messages).

Pinned here:
  - banner rules are entities (class BannerRule): WHERE atoms in cliffhanger's pattern language
    (isa, is, filter) plus the graph's edge(?a, rel, ?b) (rel may end in *), recent(?m) (this
    chat's last 8 messages, newest first) and mine(?r) (this person's pronoun registers), and a
    `show` line filled from the bound entities;
  - the seeded rules: open decisions this chat asked, what each pronoun holds, what the recent
    messages named (with its state), the programs the recent turns ran (with their rc);
  - a rule anyone defines works the same way; a rule that can't bind is refused;
  - the lane rides a chat turn's context; other turns don't get it.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_conversation_graph import SID, _seat  # noqa: E402
from test_pronoun_registers import _say  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import graphbanner as GB  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402

REPLY = "Here.\n\nOpen decisions:\n1. Should I merge PR #803?"


def _lines(srv):
    return GB.lines(srv, SID)


class Seeded(unittest.TestCase):
    def setUp(self):
        self.srv, self.runner = _seat()
        GB.seed(self.srv.entity_store)

    def test_the_rules_are_entities(self):
        names = sorted((d.properties or {}).get("name") for d in self.srv.entity_store.docs.values()
                       if d.cls == GB.CLASS_RULE)
        self.assertEqual(names, sorted(r["name"] for r in GB.SEED))

    def test_an_open_decision_shows_and_an_answered_one_doesnt(self):
        _say(self.srv, self.runner, 1, "go", REPLY)
        self.assertIn("open decision: Should I merge PR #803?", _lines(self.srv))
        _say(self.srv, self.runner, 2, "1. yes", "merging")
        self.assertNotIn("open decision: Should I merge PR #803?", _lines(self.srv))

    def test_what_a_pronoun_holds(self):
        _say(self.srv, self.runner, 1, "look", "I changed membrane/a.py.")
        _say(self.srv, self.runner, 2, "delete it", "Deleted.")
        self.assertIn('"it" = membrane/a.py', _lines(self.srv))

    def test_what_the_recent_messages_named_with_its_state(self):
        est = self.srv.entity_store
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:beta", cls="Goal",
                                 properties={"name": "beta", "status": "open"}))
        _, a = _say(self.srv, self.runner, 1, "look", "x") or (None, None)
        a = CG.msg_eid("msg_a1")
        R.upsert(self.srv.efield.b, self.srv.efield.origin, a, "refers-to", MEMBRANE_NS + "goal:beta", "chat")
        self.assertIn("named: beta (open)", _lines(self.srv))

    def test_a_program_the_turn_ran_with_its_rc(self):
        est, be, o = self.srv.entity_store, self.srv.efield.b, self.srv.efield.origin
        root, res = MEMBRANE_NS + "node:sh:abc", MEMBRANE_NS + "result:abc:1"
        est.upsert(QuantumEntity(eid=root, cls="Component", properties={"name": "pytest tests/x.py"}))
        est.upsert(QuantumEntity(eid=res, cls="Component", properties={"name": "run 1", "rc": 1}))
        self.runner._current_turn_sid = SID
        CG.note_root(self.srv, root)
        self.runner._current_turn_sid = None
        _say(self.srv, self.runner, 1, "run the tests", "one failed")
        R.upsert(be, o, root, "result", res, "code")
        self.assertIn("ran: pytest tests/x.py (rc 1)", _lines(self.srv))

    def test_the_walk_looks_back_eight_messages(self):
        _say(self.srv, self.runner, 1, "look", "I changed membrane/old.py.")
        for n in range(2, 7):
            _say(self.srv, self.runner, n, f"step {n}", "ok")
        self.assertFalse([ln for ln in _lines(self.srv) if "old.py" in ln])

    def test_the_lane_is_capped(self):
        names = " ".join(f"membrane/f{i}.py" for i in range(30))
        _say(self.srv, self.runner, 1, "look", f"I changed {names}.")
        self.assertLessEqual(len(_lines(self.srv)), GB.LINES)


class TheCost(unittest.TestCase):
    """Live, 2026-11-01: 12 lines in 1,167 ms -- every rule re-read every message's edges.
    A message's edges are read once per lane build."""

    def test_each_entitys_edges_are_read_once(self):
        srv, runner = _seat()
        GB.seed(srv.entity_store)
        for n in range(1, 5):
            _say(srv, runner, n, f"look {n}", f"I changed membrane/f{n}.py.")
        be = srv.efield.b
        real, seen = be.scan, []

        def scan(coll, eq, *a, **k):
            if coll == "relations":
                seen.append((eq.get("subj"), eq.get("obj")))
            return real(coll, eq, *a, **k)
        be.scan = scan
        GB.lines(srv, SID)
        self.assertEqual(len(seen), len(set(seen)))


class AnyonesRule(unittest.TestCase):
    def test_a_defined_rule_works_like_a_seeded_one(self):
        srv, runner = _seat()
        est = srv.entity_store
        GB.define(est, "goals named here", [["recent", "?m"], ["edge", "?m", "refers-to", "?g"],
                                             ["isa", "?g", "Goal"], ["filter", "g.status == 'blocked'"]],
                  "blocked goal: {g.name}")
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "goal:stuck", cls="Goal",
                                 properties={"name": "stuck", "status": "blocked"}))
        _say(srv, runner, 1, "look", "x")
        R.upsert(srv.efield.b, srv.efield.origin, CG.msg_eid("msg_a1"), "refers-to", MEMBRANE_NS + "goal:stuck", "chat")
        self.assertEqual(GB.lines(srv, SID), ["blocked goal: stuck"])

    def test_a_rule_that_cant_bind_is_refused(self):
        srv, _runner = _seat()
        from membrane.ch import Refused
        with self.assertRaises(Refused):
            GB.define(srv.entity_store, "loose", [["edge", "?a", "refers-to", "?b"]], "{b.name}")
        with self.assertRaises(Refused):
            GB.define(srv.entity_store, "unknown", [["near", "?a"]], "{a.name}")


class TheContext(unittest.TestCase):
    def test_a_chat_turn_carries_the_lane_and_others_dont(self):
        from membrane import lanes as L
        srv, runner = _seat()
        GB.seed(srv.entity_store)
        _say(srv, runner, 1, "look", "I changed membrane/a.py.")
        srv._cg_turn = {SID: {}}
        ctx = L._with_referent(srv, {"_turn_sid": SID}, "BANNER", False)
        self.assertIn(GB.HEADER, ctx)
        self.assertIn("named: membrane/a.py", ctx)
        self.assertEqual(L._with_referent(srv, {"_turn_sid": "ses_loop"}, "BANNER", False), "BANNER")


if __name__ == "__main__":
    unittest.main()
