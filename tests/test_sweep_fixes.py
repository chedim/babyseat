"""THE 2026-10-11 SWEEP, BATCH 1: what three read-only sweeps found failing
silently, each pinned through the path that runs in the seat."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import consequences as CQ  # noqa: E402
from membrane import llm as L  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import restarts as RS  # noqa: E402
from membrane import server as SV  # noqa: E402
from membrane import thinker as TH  # noqa: E402
from membrane import toolset as TS  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def depth(self):
        return len(self.items)


class _Srv:
    def __init__(self):
        d = Path(tempfile.mkdtemp())
        self._models_path = str(d / "models.json")
        self.entity_store = EntityStore(path=str(d / "e.json"), shared=False)
        self.entity_store._srv = self
        self.events_pool = _Pool()
        self.paused = False


class TestFieldChangedReachesThePool(unittest.TestCase):
    def test_delivered_through_the_live_pool(self):
        srv = _Srv()                               # no _events_pool attribute: the live seat's shape
        ent = QuantumEntity(eid=MEMBRANE_NS + "Component:x", cls="Component",
                            properties={"name": "x", "watch_fields": [{"verb": "pos", "arg": 0.01}]})
        srv.entity_store.docs[ent.eid] = ent
        ent.position = [1.0, 0.0]
        N.note_field_change(srv, srv.entity_store, ent, change_ts=1000.0)
        ent.position = [0.0, 1.0]                  # #567: only a crossing delivers
        N.note_field_change(srv, srv.entity_store, ent, change_ts=1001.0)
        self.assertTrue([e for e in srv.events_pool.items if e["kind"] == "field:changed"])

    def test_the_boot_no_longer_resets_the_pool(self):
        src = (ROOT / "membrane" / "opencode.py").read_text()
        self.assertNotIn("srv._events_pool = None", src)


class TestThinkerRepairs(unittest.TestCase):
    def test_a_wakeup_is_recorded(self):
        srv = type("S", (), {"ambient_mode": "night"})()
        kind = next(iter(TH.WAKEUP_SIGNALS))
        self.assertTrue(TH.note_wakeup_signal(srv, kind, {"a": 1}))
        self.assertEqual((srv.ambient_mode, srv._wakeups[-1]["kind"]), ("day", kind))

    def test_imports(self):
        self.assertTrue(hasattr(TH, "sys"))


class TestRestartsBootOrder(unittest.TestCase):
    def test_the_panel_waits_for_the_boot(self):
        RS._SEAT.pop("booted", None)
        self.assertFalse(RS.booted())
        srv = _Srv()
        with mock.patch.object(RS, "_send", lambda *a, **k: None), \
                mock.patch.object(RS, "boot_text", lambda s: "x"):
            RS.on_boot(srv)
        self.assertTrue(RS.booted())

    def test_a_pending_file_without_a_stamp_keeps_a_stable_one(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, RS.PENDING_FILE), "w") as f:
            json.dump({"why": "x"}, f)
        with mock.patch.object(RS, "_dir", lambda srv: d):
            a = RS._pending(None)["staged_at"]
            time.sleep(0.01)
            self.assertEqual(RS._pending(None)["staged_at"], a)


class TestConsequencesKeepWhatArrivesMidSettle(unittest.TestCase):
    def test_a_record_added_while_settling_survives(self):
        srv = _Srv()
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:pump", cls="Component", properties={"name": "pump", "state": "on"})
        srv.entity_store.docs[e.eid] = e
        CQ.record(srv, e.eid, "x", session="s1", kind="done", now=1e9)
        real = CQ.settle_eval

        def slow(*a, **k):
            CQ.record(srv, e.eid, "added meanwhile", session="s2", kind="done", now=1e9 + 3650)
            return real(*a, **k)
        with mock.patch.object(CQ, "settle_eval", slow):
            CQ.settle(srv, now=1e9 + 3700)
        pend = srv.entity_store.docs[MEMBRANE_NS + "consequences"].properties["pending"]
        self.assertIn("added meanwhile", [r["what"] for r in pend])


class TestWakesAreNotLost(unittest.TestCase):
    def setUp(self):
        L._calls.clear()
        L._echo.clear()

    def test_a_wake_refused_by_the_budget_is_deferred(self):
        srv = _Srv()
        L.set_wake(srv, "", "in 1h", "later", caller="argus", now=1e9)
        with mock.patch("membrane.opencode.nerve_paused", lambda s: False), \
                mock.patch.object(L, "_budget", lambda c, n: "the hour's budget is spent (120 calls)"):
            self.assertEqual(L.fire_due(srv, now=1e9 + 3700), [])
        w = srv.entity_store.docs[MEMBRANE_NS + "llm"].properties["wakes"]
        self.assertEqual((len(w), w[0]["deferred"]), (1, 1))
        self.assertAlmostEqual(w[0]["at"], 1e9 + 3700 + L.DEFER_S, places=0)

    def test_an_error_keeps_it(self):
        srv = _Srv()
        L.set_wake(srv, "", "in 1h", "later", now=1e9)
        with mock.patch.object(L, "request", side_effect=RuntimeError("boom")):
            L.fire_due(srv, now=1e9 + 3700)
        self.assertEqual(len(srv.entity_store.docs[MEMBRANE_NS + "llm"].properties["wakes"]), 1)


class TestTheScriptAPI(unittest.TestCase):
    def test_get_run_true_json_names_and_the_timeline(self):
        from membrane import watch_agents as WA
        srv = _Srv()
        WA._getter_last_ts.clear()
        a = QuantumEntity(eid=MEMBRANE_NS + "f:s", cls="Component",
                          properties={"name": "f:s", "getter_script": "result = {'state': {'v': 5}}"})
        b = QuantumEntity(eid=MEMBRANE_NS + "f:t", cls="Component", properties={
            "name": "f:t",
            "tick_script": ("x = membrane.entities.get('f:s', run=true)\n"
                            "w = membrane.timeline.walk(3)\n"
                            "result = {'patch': {'script_data': {'v': (x['properties'].get('script_data') or {}).get('v'),"
                            " 'walk': len(w)}}}")})
        for e in (a, b):
            srv.entity_store.docs[e.eid] = e
        out = N.run_entity_update_script(srv, b, "tick")
        self.assertNotIn("error", out, out)
        self.assertEqual(out["patch"]["script_data"]["v"], 5)        # fresh, from the run


class TestToolNames(unittest.TestCase):
    def test_invented_prefixes_drop(self):
        for raw, want in (("mem_exec", "exec"), ("m:tool:find", "find"), ("membrane_entity_add", "entity_add"),
                          ("mem_goal", "goal"), ("memory", "memory")):
            self.assertEqual(TS.canonical(raw, {})[0], want, raw)

    def test_an_unknown_name_says_so(self):
        with mock.patch.object(SV, "_lane_gate_off", lambda: False):
            msg = SV._lane_refusal("frobnicate", "operator")
        self.assertIn("no tool is named", msg)
        self.assertIn("never ran", msg)


class TestTheNowLine(unittest.TestCase):
    def test_the_seats_own_tool_names_never_rank(self):
        from membrane import entity_block as EB

        class _St:
            tier = "live"

        class _F:
            def changes_since(self, s):
                return {"s:?entity_read": [1] * 90, "s:?inlet_check": [1] * 70, "ha:light.aft": [1] * 3}

            def get(self, eid):
                return _St()

            def is_coherent(self, st):
                return False
        line = EB.now_line(_F(), 0)
        self.assertIn("ha:light.aft", line)
        self.assertNotIn("entity_read", line)
        self.assertNotIn("inlet_check", line)          # unresolved, incoherent: not the subject


if __name__ == "__main__":
    unittest.main()
