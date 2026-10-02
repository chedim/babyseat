"""THE SEAT-FIRST FIELD (the operator's 2026-10-01 design): the two-tier
structure. Tier 1 (LOCAL, authoritative): everything a seat creates lives
in its own store -- upsert NEVER writes the cloud; couchbase down changes
nothing. Tier 2 (GLOBAL): couchbase holds ONLY PUBLISHED entities -- the
model's explicit exports (per-origin pub: keys, no write contention by
construction) plus the seat's automatic presence entity. Reading global
is a pull: foreign published entities are IMPORTED into the local graph
(the same shared-vocabulary eid) where they RUN LOCALLY."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane.entities import (EntityStore, QuantumEntity, MEMBRANE_NS,
                               CLASS_COMPONENT)


class _Coll:
    """A fake couchbase collection: captures upserts/removes, answers the
    N1QL pull with canned published rows."""

    def __init__(self, rows=None):
        self.docs = {}
        self.upserts = []
        self.removes = []
        self.rows = rows or []

    def upsert(self, key, doc):
        self.upserts.append((key, doc))
        self.docs[key] = doc

    def remove(self, key):
        self.removes.append(key)
        self.docs.pop(key, None)

    def query(self, q, **kw):
        return iter(list(self.rows))


class _Store(EntityStore):
    """shared=False store with a FORCED couchbase collection (the fake) --
    publish/read lanes act as-if couchbase is up; regular upserts never
    touch it (that is the point under test)."""

    def __init__(self, origin="esphome@mac", **kw):
        super().__init__(origin=origin, shared=False, **kw)
        self.coll = _Coll()
        self._entities_collection = lambda: self.coll


def world():
    est = _Store()
    ent = QuantumEntity(eid=f"{MEMBRANE_NS}files:router.py",
                        cls=CLASS_COMPONENT,
                        properties={"name": "router.py",
                                    "summary": "picks the free model"})
    est.upsert(ent)
    return est, ent


class TestLocalFirst(unittest.TestCase):
    def test_upsert_never_touches_the_cloud(self):
        """Tier 1 is authoritative: ANY upsert (births, model edits, night
        materialization) is local-only. The couchbase lane is publish's,
        not upsert's."""
        est, _ = world()
        self.assertEqual(est.coll.upserts, [])
        est.upsert(QuantumEntity(eid=f"{MEMBRANE_NS}files:new.py",
                                 cls=CLASS_COMPONENT,
                                 properties={"name": "new.py"}))
        est.upsert(est.docs[f"{MEMBRANE_NS}files:new.py"])   # a merge
        self.assertEqual(est.coll.upserts, [])

    def test_publish_is_explicit_and_namespaced(self):
        est, _ = world()
        r = est.publish("files:router.py")
        self.assertTrue(r["ok"])
        self.assertEqual(r["ver"], 1)
        keys = [k for k, _ in est.coll.upserts]
        # PER-ORIGIN KEYS: two seats publishing 'router.py' never fight
        self.assertEqual(
            keys, [f"pub:esphome@mac:{MEMBRANE_NS}files:router.py"])
        doc = est.coll.docs[keys[0]]
        self.assertEqual(doc["published"]["by"], "esphome@mac")
        self.assertEqual(doc["published"]["eid"],
                         f"{MEMBRANE_NS}files:router.py")
        self.assertEqual(doc["published"]["ver"], 1)
        # the local doc learns it is published
        local = est.docs[f"{MEMBRANE_NS}files:router.py"]
        self.assertEqual((local.properties or {}).get("published_ver"), 1)

    def test_republish_bumps_the_version(self):
        est, _ = world()
        est.publish("files:router.py")
        est.docs[f"{MEMBRANE_NS}files:router.py"].properties["summary"] = "v2"
        r = est.publish("files:router.py")
        self.assertEqual(r["ver"], 2)
        doc = est.coll.docs[r["key"]]
        self.assertEqual(doc["published"]["ver"], 2)
        self.assertEqual(doc["data"]["properties"]["summary"], "v2")

    def test_unpublish_retracts(self):
        est, _ = world()
        est.publish("files:router.py")
        r = est.unpublish("files:router.py")
        self.assertTrue(r["ok"])
        self.assertEqual(est.coll.removes,
                         [f"pub:esphome@mac:{MEMBRANE_NS}files:router.py"])
        local = est.docs[f"{MEMBRANE_NS}files:router.py"]
        self.assertIsNone((local.properties or {}).get("published_ver"))

    def test_publish_degrades_when_couchbase_is_down(self):
        est, _ = world()
        est._entities_collection = lambda: None
        r = est.publish("files:router.py")
        self.assertFalse(r["ok"])
        self.assertIn("unavailable", r["error"])
        r2 = est.unpublish("files:router.py")
        self.assertFalse(r2["ok"])

    def test_edit_is_the_model_vehicle(self):
        """The model publishes THROUGH the edit surface -- one verb, the
        model's decision (never auto)."""
        est, _ = world()
        r = est.edit("files:router.py", publish=True)
        self.assertTrue(r["ok"])
        self.assertEqual(len(est.coll.upserts), 1)
        est.edit("files:router.py", publish=True, unpublish=True)
        self.assertEqual(len(est.coll.removes), 1)


class TestImport(unittest.TestCase):
    """READING global is a pull: foreign published entities map onto the
    local graph (the shared-vocabulary eid) and run locally."""

    def _foreign_rows(self, other):
        """A seat on the same bucket with one published entity. The fake's
        query YIELDS N1QL-SHAPED ROWS (dicts with an ``id`` key, doc
        fields spread flat) -- that is the shape _pub_query consumes."""
        other.upsert(QuantumEntity(eid=f"{MEMBRANE_NS}files:gate.py",
                                   cls=CLASS_COMPONENT,
                                   properties={"name": "gate.py",
                                               "summary": "the request gate"}))
        other.publish(f"{MEMBRANE_NS}files:gate.py")

        class _CL(_Coll):
            def __init__(self, docs):
                super().__init__()
                self.docs = dict(docs)

            def query(self, q, **kw):
                return iter([{"id": k, **v} for k, v in
                             list(self.docs.items())])

        return _CL(other.coll.docs)

    def test_foreign_published_imports_runs_locally(self):
        other = _Store(origin="laptop@mac")
        est = _Store(origin="esphome@mac")
        est._entities_collection = lambda: self._foreign_rows(other)
        n = est.import_published()
        self.assertEqual(n, 1)
        imp = est.docs.get(f"{MEMBRANE_NS}files:gate.py")
        self.assertIsNotNone(imp)
        self.assertEqual((imp.properties or {}).get("imported_by"),
                         "laptop@mac")
        self.assertEqual((imp.properties or {}).get("summary"),
                         "the request gate")
        # re-import is idempotent; nothing local is clobbered
        self.assertEqual(est.import_published(), 0)
        est.docs[f"{MEMBRANE_NS}files:gate.py"].properties["summary"] = \
            "the request gate (localized)"
        est.import_published()
        self.assertEqual((imp.properties or {}).get("summary"),
                         "the request gate (localized)")

    def test_no_code_imports_state_only(self):
        """THE NO-INFECTION RULE (the operator's 2026-10-01 call): a
        published doc carries NO executable code -- imports take the
        LAST STATE (summary, props, render) but never the foreign
        seat's scripts (a foreign tick_script importing itself would
        otherwise RUN inside the host seat). Stripped on publish (the
        wire never carries code) AND on import (old publishers defend)."""
        other = _Store(origin="laptop@mac")
        other.upsert(QuantumEntity(
            eid=f"{MEMBRANE_NS}tool:counter", cls="Tool",
            properties={"name": "counter",
                        "summary": "counts the seat's beats",
                        "tick_script": "result = {'render': 'bad code'}",
                        "nudge_script": "result = {'delete': True}",
                        "script_disabled": False, "script_failures": 2,
                        "progress": 40}))
        other.publish(f"{MEMBRANE_NS}tool:counter")
        rows = self._foreign_rows(other)
        # the WIRE carries no code
        key = f"pub:laptop@mac:{MEMBRANE_NS}tool:counter"
        wire_props = rows.docs[key]["data"]["properties"]
        self.assertNotIn("tick_script", wire_props)
        self.assertNotIn("nudge_script", wire_props)
        self.assertNotIn("script_disabled", wire_props)
        est = _Store(origin="esphome@mac")
        est._entities_collection = lambda: rows
        n = est.import_published()
        self.assertEqual(n, 2)   # the counter + the helper's own gate.py
        imp = est.docs[f"{MEMBRANE_NS}tool:counter"]
        props = imp.properties or {}
        self.assertNotIn("tick_script", props)
        self.assertNotIn("nudge_script", props)
        self.assertNotIn("script_failures", props)
        # ... the STATE rides
        self.assertEqual(props.get("progress"), 40)
        self.assertEqual(props.get("summary"), "counts the seat's beats")

    def test_own_published_docs_are_not_reimported(self):
        """The local doc is ALWAYS the authority -- the seat never
        re-imports its own exports."""
        est, _ = world()

        class _Q:
            def query(self, q, **kw):
                return iter([{"id": k, **v} for k, v in
                             list(est.coll.docs.items())])

        est._entities_collection = lambda: _Q()
        self.assertEqual(est.import_published(), 0)


class TestPresence(unittest.TestCase):
    """THE PRESENCE ORGAN (the operator's 2026-10-01 design): the seat's
    automatic heart published to the global field -- who is alive, what
    it runs, how long it has been up. It is the THINKER'S ANCHOR: the
    roster foreign seats read to know who is here. Not a model
    decision: the seat publishes its OWN presence on its slow cadence
    (the thinker's beat % 60 -- much slower than the 1Hz clock, which
    stays the tick primitive)."""

    def test_presence_seeds_publishes_and_version_bumps(self):
        from membrane.entities import CLASS_SEAT
        est, _ = world()
        r = est.update_presence({"branch": "entity-field",
                                 "head": "cadf54e", "uptime": 120})
        self.assertTrue(r["ok"])
        local = est.docs[f"{MEMBRANE_NS}presence"]
        self.assertEqual(local.cls, CLASS_SEAT)
        p = local.properties or {}
        self.assertEqual(p.get("seat"), "esphome@mac")
        self.assertEqual(p.get("branch"), "entity-field")
        self.assertEqual(p.get("head"), "cadf54e")
        keys = [k for k, _ in est.coll.upserts]   # ALWAYS published
        self.assertIn(f"pub:esphome@mac:{MEMBRANE_NS}presence", keys)
        wire = est.coll.docs[keys[0]]
        ver1 = wire["published"]["ver"]
        r2 = est.update_presence({"uptime": 180})
        self.assertEqual(r2["ver"], ver1 + 1)     # bump, not duplicate

    def test_peers_reads_the_roster(self):
        class _Q:
            def __init__(self, docs):
                self.docs = docs

            def query(self, q, **kw):
                return iter([{"id": k, **v} for k, v in
                             list(self.docs.items())])

        mine = _Store(origin="esphome@mac")
        mine.update_presence({"uptime": 10})
        foreign = _Store(origin="laptop@mac")
        foreign.update_presence({"uptime": 900})
        est = _Store(origin="esphome@mac")
        coll = _Q(dict(foreign.coll.docs))
        coll.docs.update(mine.coll.docs)          # own presence rides too
        est._entities_collection = lambda: coll
        peers = est.peers()
        self.assertEqual([p["origin"] for p in peers], ["laptop@mac"])
        self.assertEqual(peers[0]["properties"]["seat"], "laptop@mac")
        self.assertEqual(peers[0]["properties"]["uptime"], 900)


if __name__ == "__main__":
    unittest.main()