"""THE ATTENTION BUDGET (2026-10-08, the operator's call, red first): the
model manages its own watches -- "it should clean up watches it doesn't
need when it runs into the limit on how many items it can watch".

  * ONE budget (ATTENTION_BUDGET = 12) across the three watch senses: the
    board's spatial watches (cap 7 of its own), the relational watch_ops,
    the own-motion watch_fields. Tracks are the header's, not counted.
  * At the limit membrane REFUSES with the INVENTORY (what, owner, age,
    hits) -- it never evicts for the model (the old board dropped its
    lowest-charge watch silently).
  * Every watch carries its OWNER (the planting loop's agent), so a later
    loop can see a fulfilled goal's leftovers and retire them.
  * The board gets a real tool (field_watch) -- the LISTENERS' hand; the
    chat lane announces through `intend`.
  * The tool texts tell the truth (no retired mqtt watch kind; watching is
    the geometric/relational senses, not shell polling).
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import attention as AT  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402
from membrane.watch import MAX_WATCHES, WatchBoard, WatchFull  # noqa: E402


class _Emb:
    def embed(self, texts):
        out = []
        for t in texts:
            h = abs(hash(t))
            v = np.asarray([(h >> (4 * i)) & 0xF for i in range(8)], dtype=float) + 1.0
            out.append(list(v / np.linalg.norm(v)))
        return out


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)


class _Srv:
    pass


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = _Srv()
    srv.entity_store = EntityStore(path=str(d / "ents.json"), shared=False)
    srv.entity_store._srv = srv
    srv.events_pool = _Pool()
    srv._watch_board = WatchBoard(d / "watches.json", _Emb())
    srv.membrane = None
    srv.efield = None
    return srv


def _ent(est, eid):
    e = QuantumEntity(eid=eid, cls="Component", properties={"name": eid})
    est.docs[eid] = e
    return e


def _check(srv):
    return lambda n: AT.over(srv.entity_store, srv._watch_board, n)


class TestBoardNeverEvicts(unittest.TestCase):
    def test_full_board_refuses_with_inventory(self):
        srv = _seat()
        b = srv._watch_board
        for i in range(MAX_WATCHES):
            b.set_watch(f"topic {i}", None, owner="watch: goal:a")
        with self.assertRaises(WatchFull) as cm:
            b.set_watch("one more", None, owner="watch: goal:b")
        self.assertEqual(len(b.watches), MAX_WATCHES)        # nothing evicted
        inv = cm.exception.inventory
        self.assertEqual(len(inv), MAX_WATCHES)
        self.assertEqual({x["owner"] for x in inv}, {"watch: goal:a"})
        self.assertIn("drop", str(cm.exception))

    def test_replant_needs_no_room(self):
        srv = _seat()
        b = srv._watch_board
        for i in range(MAX_WATCHES):
            b.set_watch(f"topic {i}", None)
        w, ev = b.set_watch("topic 3", None)                  # re-arm, not new
        self.assertIsNone(ev)
        self.assertEqual(len(b.watches), MAX_WATCHES)

    def test_owner_persists(self):
        srv = _seat()
        b = srv._watch_board
        b.set_watch("anchorage", None, owner="watch: goal:x")
        b2 = WatchBoard.load(b.path, _Emb())
        self.assertEqual([w.owner for w in b2.watches.values()], ["watch: goal:x"])


class TestOneBudgetAcrossSenses(unittest.TestCase):
    def _fill_ten(self, srv):
        est = srv.entity_store
        a, f = _ent(est, "membrane::c:a"), _ent(est, "membrane::c:f")
        ops = [{"op": "near", "rel": "operator", "z": 0.1 * (i + 1), "owner": "watch: goal:o"}
               for i in range(8)]
        self.assertTrue(WA.edit_watch_ops(srv, a, ops)["ok"])
        r = est.edit_watch_fields(f.eid, [{"verb": "pos", "arg": 0.2, "owner": "watch: goal:f"},
                                          {"verb": "align", "arg": 0.5}])
        self.assertTrue(r["ok"], r)
        self.assertEqual(AT.used(est, srv._watch_board), 10)
        return a, f

    def test_the_twelfth_fits_the_thirteenth_does_not(self):
        srv = _seat()
        a, f = self._fill_ten(srv)
        b = srv._watch_board
        b.set_watch("boat", None, check=_check(srv))
        b.set_watch("anchor", None, check=_check(srv))
        self.assertEqual(AT.used(srv.entity_store, b), AT.ATTENTION_BUDGET)
        with self.assertRaises(WatchFull) as cm:
            b.set_watch("battery", None, check=_check(srv))
        senses = {x["sense"] for x in cm.exception.inventory}
        self.assertEqual(senses, {"board", "watch_ops", "watch_fields"})
        e2 = _ent(srv.entity_store, "membrane::c:b")
        r = WA.edit_watch_ops(srv, e2, [{"op": "away", "rel": "self", "y": 0.3}])
        self.assertFalse(r["ok"])
        self.assertIn("attention budget", r["error"])
        self.assertEqual(len(r["inventory"]), AT.ATTENTION_BUDGET)
        r = srv.entity_store.edit_watch_fields(e2.eid, [{"verb": "vel", "arg": 1}])
        self.assertFalse(r["ok"])
        self.assertIn("attention budget", r["error"])

    def test_dropping_frees_room(self):
        srv = _seat()
        a, f = self._fill_ten(srv)
        b = srv._watch_board
        b.set_watch("boat", None, check=_check(srv))
        b.set_watch("anchor", None, check=_check(srv))
        b.drop_watch("boat")
        w, _ = b.set_watch("battery", None, check=_check(srv))
        self.assertEqual(w.label, "battery")

    def test_replacing_in_place_costs_nothing_extra(self):
        srv = _seat()
        a, f = self._fill_ten(srv)
        b = srv._watch_board
        b.set_watch("boat", None, check=_check(srv))
        b.set_watch("anchor", None, check=_check(srv))
        r = srv.entity_store.edit_watch_fields(f.eid, [{"verb": "vel", "arg": 2},
                                                       {"verb": "pos", "arg": 0.3}])
        self.assertTrue(r["ok"], r)                          # 2 for 2: still 12

    def test_inventory_carries_owner_age_and_what(self):
        srv = _seat()
        self._fill_ten(srv)
        inv = AT.inventory(srv.entity_store, srv._watch_board)
        ops = [x for x in inv if x["sense"] == "watch_ops"]
        self.assertEqual({x["owner"] for x in ops}, {"watch: goal:o"})
        self.assertTrue(all(x["entity"] == "membrane::c:a" for x in ops))
        self.assertTrue(all("near" in x["what"] for x in ops))
        wf = [x for x in inv if x["sense"] == "watch_fields"]
        self.assertEqual(sorted(x["owner"] for x in wf), ["", "watch: goal:f"])


class TestOwnerStamp(unittest.TestCase):
    def test_the_turn_stamps_its_agent(self):
        args = {"eid": "x", "watch_ops": [{"op": "near", "rel": "self", "z": 1},
                                          {"op": "away", "rel": "self", "y": 1, "owner": "kept"}],
                "watch_fields": [{"verb": "pos", "arg": 1}]}
        out = S.stamp_watch_owner("entity_edit", args, "watch: goal:abc")
        self.assertEqual([o["owner"] for o in out["watch_ops"]], ["watch: goal:abc", "kept"])
        self.assertEqual(out["watch_fields"][0]["owner"], "watch: goal:abc")
        fw = S.stamp_watch_owner("field_watch", {"action": "plant", "topic": "t"}, "watch: goal:abc")
        self.assertEqual(fw["owner"], "watch: goal:abc")
        other = {"q": "x"}
        self.assertEqual(S.stamp_watch_owner("query", other, "watch: goal:abc"), other)


class TestHandsAndTexts(unittest.TestCase):
    def test_field_watch_is_every_build_lanes_hand(self):
        # the flood gates (2026-10-08): the chat lane plants too; plan reads
        self.assertIn("field_watch", S.LISTENER_TOOLS)
        self.assertIn("field_watch", S.OPERATOR_TOOLS)
        self.assertNotIn("field_watch", S.PLAN_TOOLS)
        tool = next(t for t in M.TOOLS if t["name"] == "field_watch")
        props = tool["inputSchema"]["properties"]
        self.assertEqual(set(props["action"]["enum"]), {"plant", "drop", "list"})
        self.assertIn("field_watch", M.TOOL_IMPLS)

    def test_no_retired_mqtt_watch_kind_on_offer(self):
        blob = json.dumps([t for t in M.TOOLS if t["name"] in ("entity_edit", "entity_watch", "field_watch")])
        self.assertNotIn("mqtt", blob.lower())

    def test_watching_is_not_sold_as_shell_polling(self):
        desc = next(t for t in M.TOOLS if t["name"] == "entity_watch")["description"]
        self.assertNotIn("shell:git", desc)
        self.assertIn("field_watch", desc)                    # points at the senses


if __name__ == "__main__":
    unittest.main()
