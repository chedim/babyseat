"""THE FIELD'S WIRING, END TO END (2026-10-11, the operator: "proximity
watchers not firing is a huge fuckup ... as well as thinker/dreamer
scripts! you broke the shit out of my field and wrote tests that passed
it"). sweep_watch_ops, thought_script and dreamed_script were written on
10-02 with tests that called them directly -- and no caller in the seat
ever did. These tests drive the REAL loops (the thinker's hum, the
engine's script run, the dream digest), and the inventory at the bottom
fails when anything plantable has no caller."""

import os
import re
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import night as N  # noqa: E402
from membrane import score as SC  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.acts import ActTable  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from membrane.thinker import Thinker  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def depth(self):
        return len(self.items)


class _Srv:
    def __init__(self):
        self.membrane = type("M", (), {"store": type("S", (), {"exemplars": []})()})()
        self.entity_store = EntityStore(bucket=f"t-wire-{time.time_ns()}", shared=False)
        self.entity_store._srv = self
        self.events_pool = _Pool()
        self._speak_queue = []
        self._act_table = ActTable()
        self.backend = "http://127.0.0.1:1"
        self.idle_cortex_model = ""
        self.thinker = None
        self.ambient_night = False
        self.ambient_mode = "day"
        self.ambient_pulse = (1.0, 30.0)
        self.last_turn_ts = time.time()
        self.paused = False


def _ent(srv, name, pos=None, **props):
    e = QuantumEntity(eid=MEMBRANE_NS + name, cls="Component", properties={"name": name, **props})
    if pos is not None:
        e.position = pos
    srv.entity_store.docs[e.eid] = e
    srv.entity_store.upsert(e, embedder=None)
    return e


def _hum_on_sweep(th):
    th._beat = WA.SWEEP_EVERY_BEATS - 1        # the hum's +1 lands on a sweep beat
    th._hum()


class TestProximityThroughTheHum(unittest.TestCase):
    def test_a_planted_near_watch_fires_on_the_crossing(self):
        srv = _Srv()
        _ent(srv, "f:target", pos=[1.0] + [0.0] * 7)
        w = _ent(srv, "f:watched", pos=[0.0, 1.0] + [0.0] * 6)
        WA.edit_watch_ops(srv, w, [{"op": "near", "rel": "f:target", "z": 0.25}])
        th = Thinker(srv)
        _hum_on_sweep(th)                          # baseline: far
        self.assertFalse([e for e in srv.events_pool.items if e.get("kind") == "near"])
        w.position = [0.99, 0.1] + [0.0] * 6       # it moved close
        _hum_on_sweep(th)
        near = [e for e in srv.events_pool.items if e.get("kind") == "near"]
        self.assertEqual(len(near), 1, srv.events_pool.items)
        self.assertEqual(near[0]["entity"], w.eid)

    def test_an_op_planted_before_the_wiring_stays_dormant(self):
        srv = _Srv()
        _ent(srv, "f:target", pos=[1.0] + [0.0] * 7)
        w = _ent(srv, "f:old", pos=[0.0, 1.0] + [0.0] * 6,
                 watch_ops=[{"op": "near", "rel": "f:target", "z": 0.25, "ts": 1.0}])   # no `armed`
        th = Thinker(srv)
        _hum_on_sweep(th)
        w.position = [0.99, 0.1] + [0.0] * 6
        _hum_on_sweep(th)
        self.assertFalse([e for e in srv.events_pool.items if e.get("kind") == "near"])
        inv = WA.paint_dormant(srv)
        self.assertEqual(inv["watch_ops"][0]["entity"], w.eid)
        self.assertIn("1 proximity", srv.entity_store.docs[MEMBRANE_NS + "dormant"].properties["state"])


class TestScriptReadsFireGetters(unittest.TestCase):
    def _wait(self, cond, s=3.0):
        t = time.time()
        while time.time() - t < s:
            if cond():
                return True
            time.sleep(0.02)
        return cond()

    def test_a_tick_scripts_read_fires_the_targets_getter(self):
        srv = _Srv()
        WA._getter_last_ts.clear()
        a = _ent(srv, "f:sensor", getter_script="result = {'state': {'level': 42}}")
        b = _ent(srv, "f:poller", tick_script="x = membrane.entities.get('f:sensor')\nresult = {}")
        N.run_entity_update_script(srv, b, "tick")          # the engine's real run
        self.assertTrue(self._wait(lambda: (srv.entity_store.docs[a.eid].properties.get("script_data") or {})
                                   .get("level") == 42))
        self.assertTrue(any(k[0] == "reading" and k[1] == a.eid for k in SC._counted))   # field work

    def test_one_level_deep(self):
        srv = _Srv()
        WA._getter_last_ts.clear()
        c = _ent(srv, "f:deep", getter_script="result = {'state': {'v': 1}}")
        a = _ent(srv, "f:mid", getter_script="x = membrane.entities.get('f:deep')\nresult = {'state': {'v': 2}}")
        b = _ent(srv, "f:top", tick_script="x = membrane.entities.get('f:mid')\nresult = {}")
        N.run_entity_update_script(srv, b, "tick")
        self.assertTrue(self._wait(lambda: (srv.entity_store.docs[a.eid].properties.get("script_data") or {})
                                   .get("v") == 2))
        time.sleep(0.2)
        self.assertNotIn("v", srv.entity_store.docs[c.eid].properties.get("script_data") or {})

    def test_a_models_read_is_not_field_work(self):
        srv = _Srv()
        WA._getter_last_ts.clear()
        SC._counted.clear()
        a = _ent(srv, "f:read-by-model", getter_script="result = {'state': {'level': 7}}")
        WA.entity_read_back(srv, a)                          # by="model" (the default)
        self.assertFalse(any(k[0] == "reading" and k[1] == a.eid for k in SC._counted))


class TestTheTriggerScripts(unittest.TestCase):
    def test_thought_fires_when_the_thinker_concludes(self):
        srv = _Srv()
        e = _ent(srv, "ha:sensor.bilge")
        srv.entity_store.edit(e.eid, thought_script="result = {'patch': {'script_data': {'thought': 1}}}")
        th = Thinker(srv)
        th._log_mind({"kind": "capture", "about": "ha:sensor.bilge", "acted": "silent"})
        self.assertNotIn("thought", srv.entity_store.docs[e.eid].properties.get("script_data") or {})
        th._log_mind({"kind": "capture", "about": "ha:sensor.bilge", "acted": "pattern"})
        self.assertEqual((srv.entity_store.docs[e.eid].properties.get("script_data") or {}).get("thought"), 1)

    def test_dreamed_fires_on_a_confirmed_branch(self):
        srv = _Srv()
        e = _ent(srv, "ha:sensor.bilge")
        srv.entity_store.edit(e.eid, dreamed_script="result = {'patch': {'script_data': {'dreamed': 1}}}")
        br = srv.entity_store.make_timeline(terminal="the bilge level ha:sensor.bilge rises overnight")
        th = Thinker(srv)
        th._process_one({"kind": "dream-outcome", "payload": {"eid": br.eid, "confirmed": 1}})  # it raised before
        self.assertEqual((srv.entity_store.docs[e.eid].properties.get("script_data") or {}).get("dreamed"), 1)

    def test_a_script_set_before_the_wiring_is_dormant(self):
        srv = _Srv()
        e = _ent(srv, "ha:sensor.old", thought_script="result = {'patch': {'script_data': {'thought': 1}}}")
        Thinker(srv)._log_mind({"kind": "capture", "about": "ha:sensor.old", "acted": "pattern"})
        self.assertNotIn("thought", srv.entity_store.docs[e.eid].properties.get("script_data") or {})
        self.assertEqual(WA.dormant_inventory(srv.entity_store)["scripts"][0]["kind"], "thought")


class TestNothingPlantableIsUnwired(unittest.TestCase):
    """THE INVENTORY: every script kind the model can set and every watch op
    it can plant must have a caller in the running seat (not in tests)."""
    CALLERS = {
        "getter": r'run_entity_update_script\(\s*srv,\s*ent,\s*"getter"',
        "tick": r'run_entity_update_script\(\s*self\.srv,\s*ent,\s*"tick"',
        "mention": r'self\.srv,\s*ent,\s*"mention"',
        "watch": r'run_entity_update_script\(\s*self\.srv,\s*ent,\s*"watch"',
        "thought": r'fire_trigger\([^)]*"thought"',
        "dreamed": r'fire_trigger\([^)]*"dreamed"',
        "nudge": r"run_scripted_entities\(",
        "delete": r"run_scripted_entities\(",
    }

    def _src(self):
        return {p: p.read_text() for p in (ROOT / "membrane").glob("*.py")}

    def test_every_script_kind_has_a_caller(self):
        from membrane.toolset import SCRIPT_KINDS
        src = self._src()
        for kind in SCRIPT_KINDS:
            self.assertIn(kind, self.CALLERS, f"script kind {kind!r} is settable but declares no caller here")
            pat = re.compile(self.CALLERS[kind])
            hits = [p.name for p, t in src.items()
                    if any(pat.search(ln) and not ln.lstrip().startswith(("def ", "#")) for ln in t.splitlines())
                    or pat.search(t.replace("\n", " ")) and "def run_scripted_entities" not in t]
            self.assertTrue(hits, f"script kind {kind!r} has no caller in the seat")

    def test_every_watch_op_has_a_sweep_on_a_beat(self):
        src = self._src()
        self.assertIn("sweep_now(srv)", src[ROOT / "membrane" / "thinker.py"])
        self.assertIn("sweep_watch_ops(est, pool", src[ROOT / "membrane" / "watch_agents.py"])
        for op in ("away", "near", "around"):
            self.assertIn(f'"{op}"', src[ROOT / "membrane" / "watch_agents.py"])


if __name__ == "__main__":
    unittest.main()
