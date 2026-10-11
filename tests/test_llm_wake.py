"""SELF-SCHEDULED WAKES (2026-10-11, Argus: "I have no way to hold a goal and
wake myself to pursue it"): llm.wake(about, at, prompt) keeps a deadline on
the llm entity; at its time the prompt becomes a model turn through
request() -- on time, not only when idle; it survives a restart; a missed
one fires late, marked; unwake cancels; never a shell."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import llm as L  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

BAT = MEMBRANE_NS + "Component:bat"


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)

    def depth(self):
        return len(self.items)


class _Srv:
    def __init__(self, path=None):
        self.path = path or str(Path(tempfile.mkdtemp()) / "e.json")
        self.entity_store = EntityStore(path=self.path, shared=False)
        self.entity_store._srv = self
        self.events_pool = _Pool()
        self.paused = False
        if BAT not in self.entity_store.docs:
            bat = QuantumEntity(eid=BAT, cls="Component", properties={"name": "bat"})
            self.entity_store.docs[bat.eid] = bat
            self.entity_store.upsert(bat, embedder=None)


NOW = time.mktime((2026, 10, 11, 14, 0, 0, 0, 0, -1))


class TestParse(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(L.parse_at("in 2h", NOW), NOW + 7200)
        self.assertEqual(L.parse_at("3d", NOW), NOW + 3 * 86400)
        self.assertEqual(L.parse_at("1h30m", NOW), NOW + 5400)
        self.assertEqual(L.parse_at("45 min", NOW), NOW + 2700)
        t = L.parse_at("18:00", NOW)
        self.assertEqual(time.localtime(t)[2:5], (11, 18, 0))
        t = L.parse_at("09:00", NOW)          # already past today: tomorrow
        self.assertEqual(time.localtime(t)[2:5], (12, 9, 0))
        self.assertAlmostEqual(L.parse_at("2026-10-12T08:00:00", NOW),
                               time.mktime((2026, 10, 12, 8, 0, 0, 0, 0, -1)))
        for bad in ("", "soon", "25:00"):
            with self.assertRaises(ValueError):
                L.parse_at(bad, NOW)


class TestWakes(unittest.TestCase):
    def setUp(self):
        L._calls.clear()
        L._echo.clear()
        self._p = mock.patch("membrane.opencode.nerve_paused", lambda srv: bool(srv.paused))
        self._p.start()

    def tearDown(self):
        self._p.stop()

    def test_set_and_fire_on_time_even_when_busy(self):
        srv = _Srv()
        srv.events_pool.items.append({"kind": "busy"})       # the pool isn't idle
        r = L.set_wake(srv, "Component:bat", "in 2h", "check the charge curve", caller="argus", now=NOW)
        self.assertEqual(r["rc"], 0, r)
        ent = srv.entity_store.docs[MEMBRANE_NS + "llm"]
        self.assertIn("1 wake(s) pending", ent.properties["wake_state"])
        self.assertEqual(L.fire_due(srv, now=NOW + 3600), [])
        out = L.fire_due(srv, now=NOW + 7200 + 5)
        self.assertEqual(len(out), 1)
        ev = srv.events_pool.items[-1]
        self.assertEqual((ev["kind"], ev["entity"], ev["caller"]), ("llm-call", BAT, "wake:argus"))
        self.assertFalse(ev["wake"]["late"])
        self.assertFalse(ev["readonly"])
        self.assertEqual(ent.properties["wakes"], [])
        self.assertIn("YOUR WAKE", WA.loop_task_text(ev))

    def test_range_and_cap(self):
        srv = _Srv()
        self.assertIn("too soon", L.set_wake(srv, "", "in 2m", "x", now=NOW)["text"])
        self.assertIn("too far", L.set_wake(srv, "", "40d", "x", now=NOW)["text"])
        self.assertIn("prompt", L.set_wake(srv, "", "in 1h", "", now=NOW)["text"])
        for i in range(L.WAKES_MAX):
            self.assertEqual(L.set_wake(srv, "", f"{i + 1}h", f"p{i}", now=NOW)["rc"], 0)
        self.assertIn("pending already", L.set_wake(srv, "", "in 1h", "one more", now=NOW)["text"])

    def test_survives_a_restart_and_fires_late(self):
        srv = _Srv()
        L.set_wake(srv, "Component:bat", "in 1h", "look again", caller="argus", now=NOW)
        srv.entity_store.flush() if hasattr(srv.entity_store, "flush") else None
        srv2 = _Srv(path=srv.path)                            # the reboot: a fresh store, same file
        self.assertEqual(len(srv2.entity_store.docs[MEMBRANE_NS + "llm"].properties["wakes"]), 1)
        out = L.fire_due(srv2, now=NOW + 3600 + 900)
        self.assertTrue(out[0]["late"])
        self.assertIn("LATE", WA.loop_task_text(srv2.events_pool.items[-1]))

    def test_paused_or_no_pool_keeps_it(self):
        srv = _Srv()
        L.set_wake(srv, "", "in 1h", "later", now=NOW)
        srv.paused = True
        self.assertEqual(L.fire_due(srv, now=NOW + 4000), [])
        self.assertEqual(len(srv.entity_store.docs[MEMBRANE_NS + "llm"].properties["wakes"]), 1)
        srv.paused = False
        self.assertEqual(len(L.fire_due(srv, now=NOW + 4100)), 1)

    def test_unwake(self):
        srv = _Srv()
        wid = L.set_wake(srv, "", "in 1h", "x", now=NOW)["text"].split()[1]
        self.assertEqual(L.unwake(srv, "nope")["rc"], 1)
        self.assertEqual(L.unwake(srv, wid)["rc"], 0)
        self.assertEqual(L.fire_due(srv, now=NOW + 7200), [])

    def test_script_wake_fires_readonly(self):
        srv = _Srv()
        L.set_wake(srv, "", "in 1h", "x", caller="script:bat", now=NOW)
        L.fire_due(srv, now=NOW + 3700)
        self.assertTrue(srv.events_pool.items[-1]["readonly"])

    def test_the_methods_and_the_command_never_shell(self):
        srv = _Srv()
        L.bind(srv)
        names = {(d.properties or {}).get("method_name") for d in srv.entity_store.docs.values()
                 if (d.properties or {}).get("method_of")}
        self.assertTrue({"call", "wake", "unwake"} <= names, names)
        with mock.patch.object(N.subprocess, "run", side_effect=AssertionError("a shell")):
            r = L.wake_from_command("Component:bat 'in 2h' 'check it'")
        self.assertEqual(r["rc"], 0, r)

    def test_banner_shows_the_next_wake(self):
        srv = _Srv()
        L.set_wake(srv, "Component:bat", "in 2h", "x")
        line = WA.loops_banner_line(srv)
        self.assertIn("next wake", line or "")


if __name__ == "__main__":
    unittest.main()
