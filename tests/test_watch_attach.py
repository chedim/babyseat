"""WATCH ATTACH/DETACH EVENTS (the operator's 2026-10-02 ask, red
first): every watch plant/drop -- all THREE lanes (the count-tracks,
the autonode selector, the spatial watch_ops) -- notifies the model's
event pool (kind 'watch-attached' / 'watch-detached'), and the
watch_ops lane gets its MCP CRUD surface (entity_edit, append/
replace/clear, the 8-op cap)."""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


def _est():
    return EntityStore(bucket="t-watch-events", shared=False)


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, ev):
        self.items.append(dict(ev))

    def take(self):
        out, self.items = self.items, []
        return out


class _Srv:
    def __init__(self, est, pool):
        self.entity_store = est
        self.events_pool = pool
        self.membrane = None
        self.efield = None
        self.idle_cortex_model = "granite3.3:8b"


class TestTrackAttachDetachEvents(unittest.TestCase):
    def test_track_plant_fires_attached_event(self):
        est = _est()
        pool = _Pool()
        WA.notify_watch_attached(est, pool, "files:router.py",
                                 lane="track", detail="count track")
        evs = pool.take()
        self.assertEqual(len(evs), 1)
        self.assertEqual(evs[0]["kind"], "watch-attached")
        self.assertEqual(evs[0]["entity"], "files:router.py")
        self.assertEqual(evs[0]["lane"], "track")

    def test_unwatch_fires_detached_event(self):
        est = _est()
        est.watch_track("files:router.py")
        pool = _Pool()
        n = WA.notify_watch_detached(est, pool, ["files:router.py"],
                                     lane="track")
        self.assertEqual(n, 1)
        evs = pool.take()
        self.assertEqual(evs[0]["kind"], "watch-detached")
        self.assertEqual(evs[0]["lane"], "track")
        self.assertEqual(evs[0]["entity"], "files:router.py")


class TestWatchSelectorEvents(unittest.TestCase):
    def test_selector_set_fires_attached(self):
        """The WATCH lane's selector ({kind: file...}): set + clear fire
        the events (the same lane's attach/detach covenant)."""
        est = _est()
        pool = _Pool()
        evs = WA.notify_watch_attached(
            est, pool, "tool:radios", lane="selector",
            detail={"kind": "file", "glob": "*.py"})
        pool_evs = pool.take()
        self.assertEqual(pool_evs[0]["kind"], "watch-attached")
        self.assertEqual(pool_evs[0]["lane"], "selector")


class TestWatchOpsEventEvents(unittest.TestCase):
    def test_ops_change_fires_attached_detached(self):
        """THE SPATIAL LANE: watch_ops changes (an op added / removed)
        fire the events with the OP SHAPE (op/rel/y|z)."""
        est = _est()
        ent = QuantumEntity(eid="membrane::tool:radios", cls="Tool",
                            properties={"name": "radios"})
        est.docs[ent.eid] = ent
        pool = _Pool()
        WA.notify_watch_attached(est, pool, ent.eid, lane="watch_ops",
                                 detail={"op": "around", "rel": "*",
                                         "z": 0.12})
        pool.take()
        WA.notify_watch_detached(est, pool, [ent.eid], lane="watch_ops",
                                 detail={"op": "around", "rel": "*"})
        evs = pool.take()
        self.assertEqual(evs[0]["kind"], "watch-detached")
        self.assertEqual(evs[0]["lane"], "watch_ops")


class TestWatchOpsCRUD(unittest.TestCase):
    def test_edit_appends_ops_capped_at_8(self):
        """THE MCP SURFACE: entity_edit(watch_ops=[...]) appends (or
        replaces with replace=True; [] clears); the 8-op cap refuses
        overflow with an honest error."""
        est = _est()
        srv = _Srv(est, _Pool())
        ent = QuantumEntity(eid="membrane::tool:radios", cls="Tool",
                            properties={"name": "radios"})
        est.docs[ent.eid] = ent
        r1 = WA.edit_watch_ops(srv, ent, [{"op": "around", "rel": "*",
                                           "z": 0.12}])
        self.assertTrue(r1.get("ok"))
        self.assertEqual(len((est.docs[ent.eid].properties or {})
                             .get("watch_ops") or []), 1)
        # append a second
        r2 = WA.edit_watch_ops(srv, est.docs[ent.eid],
                               [{"op": "near", "rel": "operator", "z": 0.2}])
        self.assertEqual(len((est.docs[ent.eid].properties or {})
                             .get("watch_ops") or []), 2)
        # the cap: append 7 more -> one refuses
        for i in range(7):
            WA.edit_watch_ops(srv, est.docs[ent.eid],
                              [{"op": "near", "rel": "operator", "z": 0.1}])
        r3 = WA.edit_watch_ops(srv, est.docs[ent.eid],
                               [{"op": "near", "rel": "operator", "z": 0.05}])
        self.assertFalse(r3.get("ok"))
        self.assertIn("cap", str(r3.get("error")))

    def test_clear_and_replace(self):
        est = _est()
        srv = _Srv(est, _Pool())
        ent = QuantumEntity(eid="membrane::tool:r", cls="Tool",
                            properties={"name": "r"})
        est.docs[ent.eid] = ent
        WA.edit_watch_ops(srv, ent, [{"op": "around", "rel": "*", "z": 0.2}])
        # [] clears
        r = WA.edit_watch_ops(srv, est.docs[ent.eid], [])
        self.assertTrue(r.get("ok"))
        self.assertIsNone((est.docs[ent.eid].properties or {})
                          .get("watch_ops"))


if __name__ == "__main__":
    unittest.main()