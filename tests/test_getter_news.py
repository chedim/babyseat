"""A SAME READING IS NOT NEWS (2026-10-11, red first): a watch on the phone's
battery sensor read it, the read fired the getter, the getter posted
getter-applied, the event woke the next watch -- a fresh session every 1-4
minutes redoing the same reads. Only a changed value posts the event; a
key that is only a clock changes every run and is not a change."""

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


def _world(code):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    ent = QuantumEntity(eid=MEMBRANE_NS + "Component:bat", cls="Component",
                        properties={"name": "bat", "getter_script": code})
    est.docs[ent.eid] = ent
    est._srv = srv = _Srv(est)
    return srv, est, ent


class TestGetterNews(unittest.TestCase):
    def test_the_same_reading_twice_posts_once(self):
        srv, est, ent = _world("import time\nresult = {'state': {'pct': 40, 'ts': time.time()}}")
        WA.entity_read_back(srv, ent, force=True)
        WA.entity_read_back(srv, ent, force=True)
        self.assertEqual([e["kind"] for e in srv.events_pool.items], ["getter-applied"])

    def test_a_changed_reading_posts_again(self):
        srv, est, ent = _world("result = {'state': {'pct': 40}}")
        WA.entity_read_back(srv, ent, force=True)
        est.docs[ent.eid].properties["getter_script"] = "result = {'state': {'pct': 39}}"
        WA.entity_read_back(srv, ent, force=True)
        self.assertEqual(len(srv.events_pool.items), 2)

    def test_clock_keys_are_not_a_change(self):
        self.assertFalse(WA.reading_changed({"pct": 40, "checked_at": 1}, {"pct": 40, "checked_at": 2, "ts": 3}))
        self.assertTrue(WA.reading_changed({"pct": 40}, {"pct": 41}))
        self.assertTrue(WA.reading_changed({}, {"pct": 40}))


if __name__ == "__main__":
    unittest.main()
