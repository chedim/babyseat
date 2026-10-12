"""THE SWEEP, BATCH 3 (2026-10-11): the half-wired features made real or
honestly refused -- the operator entity moves, an add keeps what it was
given, script refusals land on the entity, wakes say what changed,
proximity and field fires wake the night, the field-watch verbs read real
values, the documented knobs and kill switch work."""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import membrane.server as MS  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import thinker as TH  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _est():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


class TestTheOperatorMoves(unittest.TestCase):
    def test_a_persons_turn_moves_the_operator(self):
        est = _est()
        WA.plant_operator(est)

        class _Emb:
            def embed(self, texts):
                return [[1.0] + [0.0] * 7 for _ in texts]
        srv = type("S", (), {"entity_store": est, "membrane": type("M", (), {"embedder": _Emb()})()})()
        ladder = lambda body: {"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}]}
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder, lambda *a, **k: "ok",
                          system="", model="m", tools=[])
        r.srv = srv
        r._turn_watchdog = lambda *a, **k: None
        r.run("s1", "[HA user: Dmitrii]\nturn on the aft light")
        t = time.time()
        while time.time() - t < 3 and not (est.docs[WA.OPERATOR_EID].properties.get("messages")):
            time.sleep(0.02)
        op = est.docs[WA.OPERATOR_EID]
        self.assertEqual(op.properties["messages"], 1)
        self.assertEqual(op.properties["last_text"], "turn on the aft light")
        self.assertIsNotNone(op.position)

    def test_its_default_sensor_stays_dormant(self):
        est = _est()
        WA.plant_operator(est)
        self.assertFalse(any(o.get("armed") for o in est.docs[WA.OPERATOR_EID].properties["watch_ops"]))


class TestAnAddKeepsWhatItWasGiven(unittest.TestCase):
    def test_properties_and_watches_ride_the_add(self):
        name, args = TS.resolve("entity", {"action": "add", "name": "x", "class": "Component",
                                           "properties": {"room": "aft"},
                                           "watch_ops": [{"op": "near", "rel": "y"}]})
        self.assertEqual(name, "entity_add")
        self.assertEqual(args["properties"], {"room": "aft"})
        self.assertEqual(args["watch_ops"], [{"op": "near", "rel": "y"}])


class TestScriptRefusalsAreVisible(unittest.TestCase):
    def test_a_tick_patch_outside_the_lanes_is_noted(self):
        est = _est()
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:t", cls="Component", properties={"name": "t"})
        est.docs[e.eid] = e
        srv = type("S", (), {"entity_store": est, "efield": None, "membrane": None})()
        N.apply_entity_update(srv, e, {"patch": {"status": "done", "script_data": {"v": 1}}, "delete": True}, "tick",
                              silent=True)
        pr = e.properties["patch_refused"]
        self.assertIn("status", pr["keys"])
        self.assertTrue(any("delete" in k for k in pr["keys"]))
        self.assertEqual(e.properties["script_data"]["v"], 1)


class TestWakesSayWhatChanged(unittest.TestCase):
    def test_getter_and_field_wakes(self):
        t = WA.loop_task_text({"kind": "getter-applied", "entity": "membrane::bat", "state": {"pct": 61}})
        self.assertIn("pct=61", t)
        t = WA.loop_task_text({"kind": "field:changed", "entity": "membrane::bat", "verb": "pos", "n_changes": 3,
                               "span_s": 4.0})
        self.assertIn("'pos' crossed", t)

    def test_proximity_and_field_fires_wake_the_night(self):
        for k in ("near", "away", "around", "field:changed", "llm-call"):
            self.assertTrue(TH.is_wakeup_signal(k), k)


class TestTheFieldWatchVerbsReadRealValues(unittest.TestCase):
    def test_vel_reads_the_ring(self):
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:v", cls="Component", properties={"name": "v"})
        e.ring_velocity = lambda now: 0.5
        props = {"watch_fields": [{"verb": "vel", "arg": 0.1}], "wf_stamp_vel": 0.0}
        hits = N._field_filter_hits(e, props)
        self.assertEqual([h["verb"] for h in hits], ["vel"])


class TestTheKnobsAndTheSwitch(unittest.TestCase):
    def test_the_script_knobs_can_be_set(self):
        est = _est()
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:k", cls="Component", properties={"name": "k"})
        est.docs[e.eid] = e
        est.edit(e.eid, properties={"script_every_n": 2, "script_cooldown_s": 99999, "script_disabled": False})
        self.assertEqual(e.properties["script_every_n"], 2)
        self.assertEqual(e.properties["script_cooldown_s"], 3600.0)
        self.assertNotIn("script_disabled", e.properties)          # the rest of script_* stays untouchable

    def test_the_kill_switch_is_read(self):
        src = (ROOT / "membrane" / "opencode.py").read_text()
        self.assertIn("enabled_loops as _wa_on", src)


if __name__ == "__main__":
    unittest.main()
