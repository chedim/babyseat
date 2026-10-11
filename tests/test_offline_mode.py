"""OFFLINE MODE (2026-10-11, the walks with Gunner): the seat keeps running
without the Mac -- scripts run, the field embeds on the phone, minds wait,
strikes pause, and Argus hears the gap when it's over."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import offline as OFF  # noqa: E402
from membrane import embed as EMB  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)


class _Srv:
    def __init__(self):
        self.entity_store = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        self.entity_store._srv = self
        self.events_pool = _Pool()


def reset():
    OFF.STATE.update({"offline": False, "since": 0.0, "fails": 0, "local_url": None, "proc": None,
                      "embedded_local": 0, "held": 0})


class TestTheProbe(unittest.TestCase):
    def setUp(self):
        reset()

    def test_two_misses_offline_one_answer_back_and_argus_hears_it(self):
        srv = _Srv()
        with mock.patch.object(OFF, "probe", return_value=False), \
                mock.patch.object(OFF, "start_local", return_value="http://127.0.0.1:8090"), \
                mock.patch("membrane.seat_panel.note"):
            OFF.step(srv, "http://mac", now=1000.0)
            self.assertFalse(OFF.is_offline())                   # one miss is a hiccup
            OFF.step(srv, "http://mac", now=1020.0)
            self.assertTrue(OFF.is_offline())
            self.assertEqual(OFF.local_embed_url(), "http://127.0.0.1:8090")
        self.assertIn("OFFLINE", srv.entity_store.docs[MEMBRANE_NS + "net"].properties["state"])
        with mock.patch.object(OFF, "probe", return_value=True), mock.patch.object(OFF, "stop_local"), \
                mock.patch("membrane.restarts.post") as post, mock.patch("membrane.seat_panel.note"):
            OFF.step(srv, "http://mac", now=1020.0 + 3600)
            import time
            time.sleep(0.1)
        self.assertFalse(OFF.is_offline())
        self.assertIn("OFFLINE", post.call_args[0][1])
        self.assertIn("(60 min)", post.call_args[0][1])


class TestTheEmbedder(unittest.TestCase):
    def setUp(self):
        reset()

    def test_offline_every_embedder_embeds_on_the_phone_normalized(self):
        OFF.STATE.update({"offline": True, "local_url": "http://127.0.0.1:8090"})
        e = EMB.OllamaEmbedder(base_url="http://mac:11434")
        with mock.patch.object(OFF, "embed_local", wraps=lambda t: np.array([[3.0, 4.0]] * len(t))) as loc:
            out = e._embed_chunk(["a walk", "a dog"])
        loc.assert_called_once()
        self.assertEqual(len(out), 2)

    def test_offline_with_no_local_one_fails_fast(self):
        OFF.STATE.update({"offline": True, "local_url": None})
        with self.assertRaises(RuntimeError):
            OFF.embed_local(["x"])


class TestTheMindsWaitAndStrikesPause(unittest.TestCase):
    def setUp(self):
        reset()

    def test_a_loop_event_waits_and_is_counted_once(self):
        srv = _Srv()
        srv.loops = WA.LoopRunner(3)
        d = WA._LoopDrainer.__new__(WA._LoopDrainer)
        d.srv = srv
        OFF.STATE["offline"] = True
        ev = {"kind": "getter-applied", "entity": "membrane::Component:bat"}
        self.assertEqual(d.handle(ev), "held")
        self.assertEqual(d.handle(srv.events_pool.items[-1]), "held")
        self.assertEqual(OFF.STATE["held"], 1)
        self.assertEqual(len(srv.events_pool.items), 2)

    def test_no_strike_while_offline(self):
        srv = _Srv()
        ent = QuantumEntity(eid=MEMBRANE_NS + "Component:ha", cls="Component", properties={"name": "ha"})
        srv.entity_store.docs[ent.eid] = ent
        OFF.STATE["offline"] = True
        N._script_fail(srv.entity_store, ent)
        self.assertNotIn("script_failures", ent.properties)
        OFF.STATE["offline"] = False
        N._script_fail(srv.entity_store, ent)
        self.assertEqual(ent.properties["script_failures"], 1)

    def test_the_supervisor_starts_the_seat_without_the_mac(self):
        sh = (Path(__file__).resolve().parent.parent / "integrations/homeassistant/deploy/brain/membrane-supervise.sh").read_text()
        self.assertIn("starting the seat offline", sh)
        self.assertNotIn("down; waiting", sh)


if __name__ == "__main__":
    unittest.main()
