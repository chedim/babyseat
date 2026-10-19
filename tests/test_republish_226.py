"""#226: a REPUBLISHED entity reaches a seat that already imported it.

The publish organ versions every export (``published.ver`` bumps on each
republish); the import organ used to skip every eid already present, so a
seat that imported v1 never saw v2. Now:

- a foreign entity whose published version is NEWER than the version this
  seat last imported upgrades the local copy's STATE (never code);
- the same version is a no-op;
- an imported entity this seat EDITED locally keeps the local edit; the
  newer version is recorded as a divergence (and announced), never applied;
- this seat's own exports and its own presence are never imported;
- the pull runs on the thinker's presence pulse, not only at boot, and a
  down cluster degrades quietly.
"""

import copy
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane.entities import QuantumEntity, MEMBRANE_NS, CLASS_COMPONENT
from test_publish import _Store

GATE = f"{MEMBRANE_NS}files:gate.py"


class _Bucket:
    """One shared bucket both fake seats publish into and pull from (the
    N1QL-shaped rows: an ``id`` key, the doc's fields spread flat)."""

    def __init__(self):
        self.docs = {}
        self.queries = 0

    def upsert(self, key, doc):
        self.docs[key] = doc

    def remove(self, key):
        self.docs.pop(key, None)

    def query(self, q, **kw):
        self.queries += 1
        # a real pull hands back FRESH rows (never the stored dicts)
        return iter([{"id": k, **copy.deepcopy(v)}
                     for k, v in list(self.docs.items())])


def two_seats():
    bucket = _Bucket()
    laptop = _Store(origin="laptop@mac")
    seat = _Store(origin="esphome@mac")
    laptop._entities_collection = lambda: bucket
    seat._entities_collection = lambda: bucket
    laptop.upsert(QuantumEntity(eid=GATE, cls=CLASS_COMPONENT,
                                properties={"name": "gate.py",
                                            "summary": "the request gate",
                                            "progress": 1}))
    laptop.publish(GATE)
    return bucket, laptop, seat


def republish(laptop, **props):
    laptop.docs[GATE].properties.update(props)
    return laptop.publish(GATE)


class TestRepublishUpgrades(unittest.TestCase):
    def test_import_records_the_version_and_origin(self):
        _, _, seat = two_seats()
        self.assertEqual(seat.import_published(), 1)
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("imported_by"), "laptop@mac")
        self.assertEqual(p.get("imported_ver"), 1)

    def test_newer_version_upgrades_the_state(self):
        _, laptop, seat = two_seats()
        seat.import_published()
        r = republish(laptop, summary="the request gate, v2", progress=2)
        self.assertEqual(r["ver"], 2)
        self.assertEqual(seat.import_published(), 1)
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("summary"), "the request gate, v2")
        self.assertEqual(p.get("progress"), 2)
        self.assertEqual(p.get("imported_ver"), 2)
        self.assertEqual(p.get("imported_by"), "laptop@mac")
        self.assertTrue(p.get("guest"))

    def test_same_version_is_a_noop(self):
        bucket, _, seat = two_seats()
        seat.import_published()
        before = dict(seat.docs[GATE].properties)
        # even a tampered row at the SAME version never applies
        key = f"pub:laptop@mac:{GATE}"
        bucket.docs[key]["data"]["properties"]["summary"] = "tampered"
        self.assertEqual(seat.import_published(), 0)
        self.assertEqual(seat.docs[GATE].properties, before)

    def test_local_keys_survive_an_upgrade(self):
        """The upgrade writes the publisher's state keys; what this seat
        grew on its own copy (runtime keys, its local geometry) stays."""
        _, laptop, seat = two_seats()
        seat.import_published()
        seat.docs[GATE].properties["seen_ts"] = 123.0
        seat.docs[GATE].links = [7, 9]
        republish(laptop, summary="v2")
        seat.import_published()
        ent = seat.docs[GATE]
        self.assertEqual(ent.properties.get("seen_ts"), 123.0)
        self.assertEqual(ent.links, [7, 9])

    def test_local_edit_wins_and_the_divergence_is_recorded(self):
        _, laptop, seat = two_seats()
        seat.import_published()
        r = seat.edit(GATE, summary="the gate, as this seat sees it")
        self.assertTrue(r["ok"])
        self.assertTrue(seat.docs[GATE].properties.get("edited_locally_at"))
        seat.announcements.clear()
        republish(laptop, summary="the request gate, v2")
        self.assertEqual(seat.import_published(), 0)
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("summary"), "the gate, as this seat sees it")
        self.assertEqual(p.get("imported_ver"), 1)        # never applied
        div = p.get("import_diverged") or {}
        self.assertEqual(div.get("by"), "laptop@mac")
        self.assertEqual(div.get("ver"), 2)
        kinds = [a["kind"] for a in seat.announcements]
        self.assertEqual(kinds, ["diverged"])
        # announced ONCE per version: the next pulse stays quiet...
        seat.import_published()
        self.assertEqual([a["kind"] for a in seat.announcements],
                         ["diverged"])
        # ... and a further version records (and announces) again
        republish(laptop, summary="v3")
        seat.import_published()
        self.assertEqual(seat.docs[GATE].properties["import_diverged"]["ver"],
                         3)
        self.assertEqual([a["kind"] for a in seat.announcements],
                         ["diverged", "diverged"])

    def test_publish_only_edit_is_not_a_local_edit(self):
        """Re-exporting an import (edit(publish=True) with nothing else) is
        not a state change -- upgrades keep flowing."""
        _, laptop, seat = two_seats()
        seat.import_published()
        seat.edit(GATE, publish=True)
        self.assertIsNone(seat.docs[GATE].properties.get("edited_locally_at"))

    def test_code_is_never_imported_on_upgrade(self):
        bucket, laptop, seat = two_seats()
        seat.import_published()
        republish(laptop, summary="v2")
        # an old (or hostile) publisher's row carrying code
        key = f"pub:laptop@mac:{GATE}"
        bucket.docs[key]["data"]["properties"]["tick_script"] = \
            "result = {'delete': True}"
        bucket.docs[key]["data"]["properties"]["script_failures"] = 2
        seat.import_published()
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("summary"), "v2")
        self.assertNotIn("tick_script", p)
        self.assertNotIn("script_failures", p)

    def test_code_on_the_wire_never_lands_on_first_import(self):
        """The import-side strip (defense in depth against old or hostile
        publishers) used to be props.update(strip(props)) -- a no-op."""
        bucket, _, seat = two_seats()
        key = f"pub:laptop@mac:{GATE}"
        bucket.docs[key]["data"]["properties"]["tick_script"] = \
            "result = {'delete': True}"
        seat.import_published()
        self.assertNotIn("tick_script", seat.docs[GATE].properties)

    def test_local_script_survives_an_upgrade(self):
        """This seat's own code on an imported entity is local and stays
        (the upgrade carries state; it never removes local keys)."""
        _, laptop, seat = two_seats()
        seat.import_published()
        seat.docs[GATE].properties["tick_script"] = "result = {}"
        republish(laptop, summary="v2")
        seat.import_published()
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("summary"), "v2")
        self.assertEqual(p.get("tick_script"), "result = {}")

    def test_a_local_entity_is_never_overwritten(self):
        """The local doc stays the authority when it was never imported
        (born here) -- a foreign publication of the same eid never lands."""
        _, laptop, seat = two_seats()
        seat.upsert(QuantumEntity(eid=GATE, cls=CLASS_COMPONENT,
                                  properties={"summary": "ours"}))
        republish(laptop, summary="v2")
        seat.import_published()
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("summary"), "ours")
        self.assertNotIn("imported_by", p)

    def test_another_origin_never_upgrades_the_import(self):
        """Per-origin keys: the copy follows the seat it was imported from;
        a second publisher of the same eid does not take it over."""
        bucket, _, seat = two_seats()
        seat.import_published()
        desk = _Store(origin="desk@linux")
        desk._entities_collection = lambda: bucket
        desk.upsert(QuantumEntity(eid=GATE, cls=CLASS_COMPONENT,
                                  properties={"summary": "desk's gate",
                                              "published_ver": 9}))
        desk.publish(GATE)
        seat.import_published()
        p = seat.docs[GATE].properties
        self.assertEqual(p.get("summary"), "the request gate")
        self.assertEqual(p.get("imported_by"), "laptop@mac")

    def test_own_exports_never_reimport(self):
        bucket, laptop, _ = two_seats()
        laptop.docs[GATE].properties["summary"] = "edited after publish"
        self.assertEqual(laptop.import_published(), 0)
        self.assertEqual(laptop.docs[GATE].properties["summary"],
                         "edited after publish")

    def test_foreign_presence_never_lands_on_this_seats_presence(self):
        """Every seat's presence shares one eid: a foreign presence row is
        the roster's (peers), never an import -- periodic pulls would
        otherwise rewrite this seat's own identity."""
        bucket, laptop, seat = two_seats()
        laptop.update_presence({"uptime": 900})
        seat.import_published()
        self.assertNotIn(f"{MEMBRANE_NS}presence", seat.docs)
        seat.update_presence({"uptime": 5})
        laptop.update_presence({"uptime": 960})
        seat.import_published()
        p = seat.docs[f"{MEMBRANE_NS}presence"].properties
        self.assertEqual(p.get("seat"), "esphome@mac")
        self.assertNotIn("imported_by", p)

    def test_pre_226_imports_upgrade_by_timestamp(self):
        """A copy imported before #226 has no imported_ver: the publisher's
        ``at`` stamp (imported_at) decides newer."""
        bucket, laptop, seat = two_seats()
        seat.import_published()
        seat.docs[GATE].properties.pop("imported_ver")
        republish(laptop, summary="v2")
        seat.import_published()
        self.assertEqual(seat.docs[GATE].properties.get("summary"), "v2")
        self.assertEqual(seat.docs[GATE].properties.get("imported_ver"), 2)

    def test_down_cluster_degrades_quietly(self):
        _, _, seat = two_seats()
        seat._entities_collection = lambda: None
        self.assertEqual(seat.import_published(), 0)

        class _Boom:
            def query(self, q, **kw):
                raise RuntimeError("cluster gone")

        seat._entities_collection = lambda: _Boom()
        self.assertEqual(seat.import_published(), 0)


class TestPulseImports(unittest.TestCase):
    """The pull rides the thinker's presence pulse (once a minute), not
    only the boot read-through."""

    def test_presence_pulse_pulls_published_entities(self):
        from membrane.thinker import Thinker
        from test_thinker import _Srv
        bucket, laptop, _ = two_seats()
        srv = _Srv()
        srv._boot_ts = time.time() - 60
        est = srv.entity_store
        est._entities_collection = lambda: bucket
        with mock.patch("membrane.thinker._git_identity",
                        return_value={"branch": "main", "head": "abc1234"}):
            th = Thinker(srv)
            th._presence_pulse()
            self.assertEqual(est.docs[GATE].properties["summary"],
                             "the request gate")
            republish(laptop, summary="v2")
            th._presence_pulse()
        self.assertEqual(est.docs[GATE].properties["summary"], "v2")
        self.assertEqual(th.stats.get("imported"), 1)

    def test_presence_pulse_with_cluster_down_never_raises(self):
        from membrane.thinker import Thinker
        from test_thinker import _Srv
        srv = _Srv()
        th = Thinker(srv)
        th._presence_pulse()
        self.assertEqual(th.stats.get("imported"), 0)


if __name__ == "__main__":
    unittest.main()
