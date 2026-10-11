"""THE SEAT AS A PANEL (2026-10-11, the operator: "does the seat's entity
expose its state/controls/events?"): state refreshed onto membrane::presence,
the seat's events kept there and published, the operator's controls applied
by the seat itself."""
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import seat_panel as SP  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import voice as V  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402
from membrane.watch_agents import LoopRunner  # noqa: E402


def _seat():
    srv = types.SimpleNamespace()
    srv.entity_store = EntityStore(path=None, shared=False)
    srv.paused = False
    srv.loops = LoopRunner(max_loops=3)
    srv._models_path = str(Path(tempfile.mkdtemp()) / "models.json")
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(), lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="glm-x")
    r.srv = srv
    srv._loop_runner = r
    seen = []
    pub = r.hub.publish
    r.hub.publish = lambda ev: (seen.append(ev), pub(ev))[1]
    return srv, r, seen


class TestSeatPanel(unittest.TestCase):
    def test_the_state_lands_on_the_presence_entity(self):
        srv, r, _ = _seat()
        st = SP.refresh(srv)
        ent = srv.entity_store.docs[MEMBRANE_NS + "presence"]
        self.assertEqual(ent.properties["state"]["loops"], {"running": 0, "max": 3})
        self.assertFalse(st["paused"])
        self.assertEqual(st["loops_model"], "glm-x")
        for k in ("seat_rss_mb", "mem_available_mb", "stream_clients", "failing_scripts", "auto"):
            self.assertIn(k, st)

    def test_the_operators_controls_are_applied_by_the_seat(self):
        srv, r, seen = _seat()
        sid = r.sessions.create(title="chat")["id"]
        r._busy = {sid: 1}                                       # no rounds fire here
        out = SP.control(srv, {"paused": True, "loops_max": 2, "session": sid, "role": "predict", "auto": True})
        self.assertTrue(srv.paused)
        self.assertEqual(srv.loops.max, 2)
        self.assertEqual(V.role(r, sid), "predict")
        self.assertTrue(V.auto_on(r, sid))
        self.assertTrue(out["state"]["paused"])
        self.assertEqual(out["state"]["auto"][sid]["role"], "predict")
        kinds = [e["kind"] for e in srv.entity_store.docs[MEMBRANE_NS + "presence"].properties["events"]]
        self.assertIn("paused", kinds)
        self.assertIn("auto-chat-on", kinds)
        self.assertTrue(any(e.get("type") == "membrane.seat" for e in seen))
        SP.control(srv, {"paused": False})
        self.assertFalse(srv.paused)

    def test_an_edit_by_a_lane_is_not_a_control(self):
        srv, r, _ = _seat()
        SP.refresh(srv)
        ent = srv.entity_store.docs[MEMBRANE_NS + "presence"]
        ent.properties["state"]["paused"] = True                  # Argus writing the property
        SP.refresh(srv)
        self.assertFalse(srv.paused)
        self.assertFalse(ent.properties["state"]["paused"])       # the truth comes back

    def test_pressure_and_a_cut_client_are_events(self):
        srv, r, _ = _seat()
        SP.RSS_HIGH_MB, old = 0, SP.RSS_HIGH_MB
        try:
            SP.refresh(srv)
            SP.refresh(srv)                                       # noted once per crossing
        finally:
            SP.RSS_HIGH_MB = old
        hub = S.SSEHub()
        hub.on_lost = lambda: SP.note(srv, "stream-cut")
        q = hub.subscribe()
        for _ in range(S.SSEHub.QUEUE_MAX + 2):
            hub.publish({"type": "message.updated", "properties": {}})
        kinds = [e["kind"] for e in srv.entity_store.docs[MEMBRANE_NS + "presence"].properties["events"]]
        self.assertEqual(kinds.count("memory-pressure"), 1)
        self.assertIn("stream-cut", kinds)

    def test_a_stalled_auto_chat_is_an_event(self):
        srv, r, _ = _seat()
        sid = r.sessions.create(title="chat")["id"]
        r._auto = {sid: {"rounds": 1, "mode": "explore"}}
        V.role_set(r, sid, "predict")
        from _ef_fakes import FakeEmbedder
        srv.membrane = types.SimpleNamespace(embedder=FakeEmbedder())
        old = V.AUTO_PAUSE_S
        V.AUTO_PAUSE_S = 0.01
        try:
            V.auto_next(r, sid, "")
            end = time.time() + 2
            ev = lambda: [e["kind"] for e in ((getattr(srv.entity_store.docs.get(MEMBRANE_NS + "presence"), "properties", None) or {}).get("events") or [])]
            while time.time() < end and "auto-chat-stalled" not in ev():
                time.sleep(0.02)
        finally:
            V.AUTO_PAUSE_S = old
        self.assertIn("auto-chat-stalled", ev())


if __name__ == "__main__":
    unittest.main()


class TestTheBattery(unittest.TestCase):
    def _with(self, srv, pct, status):
        SP._bat.update(ts=time.time(), val={"percentage": pct, "status": status, "plugged": None, "temperature": 30})
        return SP.refresh(srv)

    def test_low_warns_critical_pauses_charging_resumes_and_the_operators_pause_is_kept(self):
        srv, r, _ = _seat()
        self._with(srv, 50, "DISCHARGING")
        self._with(srv, 15, "DISCHARGING")
        self._with(srv, 6, "DISCHARGING")
        self.assertTrue(srv.paused)                                       # the battery paused the seat
        self._with(srv, 10, "CHARGING")
        self.assertTrue(srv.paused)                                       # not yet past the resume mark
        self._with(srv, 16, "CHARGING")
        self.assertFalse(srv.paused)                                      # charging back: resumed
        kinds = [e["kind"] for e in srv.entity_store.docs[MEMBRANE_NS + "presence"].properties["events"]]
        self.assertIn("battery-low", kinds)
        self.assertEqual([e.get("by") for e in srv.entity_store.docs[MEMBRANE_NS + "presence"].properties["events"]
                          if e["kind"] in ("paused", "resumed")], ["battery", "battery"])
        SP.control(srv, {"paused": True})                                 # the operator's own pause...
        self._with(srv, 90, "CHARGING")
        self.assertTrue(srv.paused)                                       # ...is never lifted by the battery
        SP._bat.update(ts=0.0, val=None)

    def test_no_termux_api_no_battery(self):
        SP._bat.update(ts=0.0, val=None)
        self.assertIsNone(SP.battery(force=True) if not __import__("shutil").which("termux-battery-status") else None)


class TestTheRoute(unittest.TestCase):
    def test_the_seat_route_is_served_here_not_relayed(self):
        # it went to the /v1 proxy's read relay (404 there) -- the route lives in server.py
        self.assertNotIn("/membrane/seat", S.SEAT_READ_PATHS)
