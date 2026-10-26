"""A READING IS NOT A WAKE (#571; before it, 2026-10-11 and #549): a getter's
reading posted getter-applied and woke the next watch -- a fresh session per
tick redoing the same reads. Now a reading is stored and shown only; the
geometric watch on a moved entity is what notifies. A key that is only a
clock changes every run and is not a change."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(ev)


class _Srv:
    def __init__(self, est):
        self.entity_store = est
        self.events_pool = _Pool()
        self.membrane = None
        self.efield = None


def _world(code, **props):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    ent = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component",
                        properties={"name": "bat", "getter_script": code, **props})
    est.docs[ent.eid] = ent
    est._srv = srv = _Srv(est)
    return srv, est, ent


class TestGetterNews(unittest.TestCase):
    def test_a_changed_reading_wakes_no_loop_even_on_a_watched_entity(self):
        # #571: a reading is stored and shown, never a wake. A script that
        # wants to be heard moves the entity; the geometric watch notifies
        # (watch_fields / watch_ops). A ticking counter is silence.
        for kw in ({}, {"watch": {"kind": "file"}}, {"watch_ops": [{"op": "near", "rel": "x", "z": 1}]},
                   {"watch_fields": [{"verb": "vel", "arg": 1}]}):
            srv, est, ent = _world("result = {'state': {'beats': 1}}", **kw)
            WA.entity_read_back(srv, ent, force=True)
            est.docs[ent.eid].properties["getter_script"] = "result = {'state': {'beats': 2}}"
            WA.entity_read_back(srv, ent, force=True)
            self.assertEqual(srv.events_pool.items, [], kw)
            self.assertEqual(est.docs[ent.eid].properties["script_data"]["beats"], 2, kw)

    def test_clock_keys_are_not_a_change(self):
        self.assertFalse(WA.reading_changed({"pct": 40, "checked_at": 1}, {"pct": 40, "checked_at": 2, "ts": 3}))
        self.assertTrue(WA.reading_changed({"pct": 40}, {"pct": 41}))
        self.assertTrue(WA.reading_changed({}, {"pct": 40}))


if __name__ == "__main__":
    unittest.main()
