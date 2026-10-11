"""OUTCOME LEARNING, PHASE 1 (2026-10-11): every watch-loop wake writes one
line -- its features and what became of it -- to <data>/voice/wakes.jsonl
(the training set for the field's own react/ignore gate); goals carry who
planted them; settled consequences keep their facts as data. Logging
only: nothing changes what the seat does."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import consequences as CQ  # noqa: E402
from membrane import server as SV  # noqa: E402
from membrane import wakes as WK  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

PUMP = MEMBRANE_NS + "Component:ha:switch.bilge_pump"


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def take(self, *a, **k):
        return None

    def depth(self):
        return len(self.items)


class _Srv:
    def __init__(self, reply="ignored: nothing new"):
        d = Path(tempfile.mkdtemp())
        self._models_path = str(d / "models.json")
        self.entity_store = EntityStore(path=str(d / "e.json"), shared=False)
        self.entity_store._srv = self
        p = QuantumEntity(eid=PUMP, cls="Component", properties={"name": "bilge pump", "state": "on"})
        self.entity_store.docs[p.eid] = p
        self.loops = WA.LoopRunner(max_loops=3)
        self.events_pool = _Pool()
        self._operator_last_ts = 0.0
        self.create_loop_session = lambda agent, title=None: "ses_loop"
        self.run_loop_turn = lambda sid, text, agent: {"text": reply, "trail": ["entity_read x"]}

    def rows(self):
        p = WK.path_of(self)
        return [json.loads(l) for l in open(p)] if p.exists() else []


class TestWakeLedger(unittest.TestCase):
    def setUp(self):
        WK._seen.clear()
        WK._last_wake.clear()

    def test_a_turn_writes_its_features_and_verdict(self):
        srv = _Srv("Looked; nothing changed.\nignored: nothing new")
        WA._LoopDrainer(srv).handle({"kind": "llm-call", "entity": PUMP, "event_id": "e1", "prompt": "check",
                                     "distance": 0.31})
        r = srv.rows()
        self.assertEqual(len(r), 1)
        r = r[0]
        self.assertEqual((r["outcome"], r["kind"], r["entity"], r["cls"]), ("ignored", "llm-call", PUMP, "Component"))
        self.assertEqual(r["nums"], {"distance": 0.31})
        self.assertEqual((r["session"], r["tools"]), ("ses_loop", 1))
        self.assertTrue(r["head"])

    def test_the_second_wake_knows_the_first(self):
        srv = _Srv("DONE switched it off")
        WA._LoopDrainer(srv).handle({"kind": "llm-call", "entity": PUMP, "event_id": "e1", "prompt": "a"})
        WA._LoopDrainer(srv).handle({"kind": "llm-call", "entity": PUMP, "event_id": "e2", "prompt": "b"})
        r = srv.rows()
        self.assertEqual([x["outcome"] for x in r], ["done", "done"])
        self.assertIsNone(r[0]["since_last_s"])
        self.assertIsNotNone(r[1]["since_last_s"])
        self.assertEqual(r[1]["last_verdicts"], ["done"])

    def test_held_events_are_logged_once(self):
        srv = _Srv()
        ev = {"kind": "near", "entity": PUMP, "event_id": "e9"}
        for _ in range(3):
            WK.log(srv, ev, "held")
        self.assertEqual(len(srv.rows()), 1)

    def test_operator_events_are_not_logged(self):
        srv = _Srv()
        WA._LoopDrainer(srv).handle({"kind": "operator-message", "entity": "", "event_id": "op-1"})
        self.assertEqual(srv.rows(), [])

    def test_script_health_and_the_gate_ride_the_features(self):
        srv = _Srv()
        srv.entity_store.docs[PUMP].properties.update({"script_failures": 3, "script_disabled": True})
        from membrane.attention import AlertGate
        srv._alert_gate = AlertGate(budget=6)
        WA._LoopDrainer(srv).handle({"kind": "near", "entity": PUMP, "event_id": "e4"})
        r = srv.rows()[0]
        self.assertEqual(r["script"], {"failures": 3, "disabled": True})
        self.assertEqual((r["gate"]["budget"], r["gate"]["spent"]), (6, 0))     # as it ARRIVED, before its charge

    def test_a_folded_wake_is_labelled(self):
        srv = _Srv()
        srv._alert_gate = type("G", (), {"allow": lambda s, ev: False, "refund": lambda s: None})()
        WA._LoopDrainer(srv).handle({"kind": "near", "entity": PUMP, "event_id": "e3"})
        self.assertEqual(srv.rows()[0]["outcome"], "folded")


class TestProvenance(unittest.TestCase):
    def test_turn_origin(self):
        self.assertEqual(SV.turn_origin("[HA user: Dmitrii Chechetkin]\nplant it"), "operator")
        self.assertEqual(SV.turn_origin("", {"autoTurn": True, "continueTurn": True}), "auto")
        self.assertEqual(SV.turn_origin("event", None, None, "watch: x"), "loop")
        self.assertEqual(SV.turn_origin("hi", None, None, "build"), "model")

    def test_the_plant_carries_its_origin(self):
        from membrane import lanes as LN
        LN.note_plant_origin("  Keep the Bilge   dry ", "operator")
        self.assertEqual(LN.take_plant_origin("keep the bilge dry"), "operator")
        self.assertEqual(LN.take_plant_origin("keep the bilge dry"), "")


class TestStructuredConsequences(unittest.TestCase):
    def test_settled_keeps_its_parts_and_active(self):
        srv = _Srv()
        srv.efield = None
        CQ.record(srv, PUMP, "x", session="s1", kind="done", now=1e9, active=[PUMP, "w:bilge"])
        CQ.settle(srv, now=1e9 + 3700)
        st = srv.entity_store.docs[MEMBRANE_NS + "consequences"].properties["settled"][-1]
        self.assertEqual(st["active"], [PUMP, "w:bilge"])
        self.assertTrue(st["parts"]["held"])
        self.assertEqual(st["parts"]["again"], 0)
        self.assertNotIn("line", st["parts"])


if __name__ == "__main__":
    unittest.main()
