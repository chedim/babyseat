"""membrane/efstore.py's Couchbase home against an in-process fake cluster:
the SQL it emits, idempotent infra, CAS under a racing writer, the applier
lease, parity with the RAM backend, and open_backend's guards."""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import CouchbaseBackend, MemBackend, knn_filter, scan_sql  # noqa: E402
from _ef_fakes import FakeCluster, FakeEmbedder  # noqa: E402


def cb():
    return CouchbaseBackend(FakeCluster(), "bkt")


class TestSQL(unittest.TestCase):
    def test_scan_sql(self):
        sql, p = scan_sql("b", "membrane", "events", {"origin": "o", "status": ["a", "b"]},
                          order=("seq",), limit=5)
        self.assertEqual(sql, "SELECT META(t).id AS k, t AS d FROM `b`.`membrane`.`events` t "
                              "WHERE t.`origin` = $p0 AND t.`status` IN $p1 ORDER BY t.`seq` LIMIT 5")
        self.assertEqual(p, {"p0": "o", "p1": ["a", "b"]})

    def test_range_filters(self):
        sql, p = scan_sql("b", "membrane", "entity_deltas", {"origin": "o", "seq": {"gt": 7, "lte": 9}})
        self.assertIn("t.`seq` > $p1_0 AND t.`seq` <= $p1_1", sql)
        self.assertEqual(p, {"p0": "o", "p1_0": 7, "p1_1": 9})
        for b in (MemBackend(), cb()):
            b.upsert_multi("entity_deltas", {f"d{i}": {"origin": "o", "seq": i} for i in range(12)})
            b.upsert("entity_deltas", "none", {"origin": "o"})
            got = sorted(d["seq"] for _, d in b.scan("entity_deltas", {"origin": "o", "seq": {"gt": 7, "lte": 9}}))
            self.assertEqual(got, [8, 9], type(b).__name__)

    def test_knn_filter(self):
        self.assertEqual(knn_filter({"tier": "live"}), {"term": "live", "field": "tier"})
        f = knn_filter({"origin": "o", "kind": ["w", "p"]})
        self.assertEqual(f["conjuncts"][1]["disjuncts"][1], {"term": "p", "field": "kind"})
        self.assertIsNone(knn_filter(None))


class TestInfra(unittest.TestCase):
    def test_infra_is_idempotent(self):
        cl = FakeCluster()
        first = CouchbaseBackend(cl, "bkt").created
        self.assertIn("scope membrane", first)
        self.assertIn("collection entity_states", first)
        self.assertEqual(CouchbaseBackend(cl, "bkt").created, [])
        self.assertTrue(any("CREATE INDEX `ef_events_origin_status_seq`" in s for s in cl.statements))


class TestKV(unittest.TestCase):
    def test_basic_ops(self):
        b = cb()
        self.assertTrue(b.insert_if_absent("events", "k", {"a": 1}))
        self.assertFalse(b.insert_if_absent("events", "k", {"a": 2}))
        self.assertEqual(b.get("events", "k"), {"a": 1})
        b.upsert_multi("events", {"x": {"a": 3}, "y": {"a": 4}})
        self.assertEqual(sorted(b.get_multi("events", ["x", "y", "nope"])), ["x", "y"])
        b.delete("events", "x")
        self.assertIsNone(b.get("events", "x"))

    def test_cas_update_survives_a_racing_writer(self):
        b = cb()
        b.upsert("origin_stats", "c", {"n": 0, "tags": []})
        coll = b._c("origin_stats")

        def racer(c):                                 # someone else writes first
            d = c.docs["c"]
            c.upsert("c", {"n": d["n"] + 10, "tags": d["tags"] + ["them"]})
        coll.before_replace = racer
        b.cas_update("origin_stats", "c", lambda d: {"n": d["n"] + 1, "tags": d["tags"] + ["us"]})
        self.assertEqual(b.get("origin_stats", "c"), {"n": 11, "tags": ["them", "us"]})

    def test_lease(self):
        b = cb()
        self.assertTrue(b.lease("applier:x", "A", 60))
        self.assertFalse(b.lease("applier:x", "B", 60))
        self.assertTrue(b.lease("applier:x", "A", 60))        # renewal
        b.upsert("origin_stats", "lease:applier:x", {"holder": "A", "until": time.time() - 1})
        self.assertTrue(b.lease("applier:x", "B", 60))        # expired


class TestParity(unittest.TestCase):
    def test_couchbase_equals_ram(self):
        texts = ["please update membrane/router.py for the gateway",
                 "the gateway keeps timing out", "the gateway returns errors today",
                 "restart the gateway after lunch"]
        out = []
        for be in (MemBackend(), cb()):
            f = EF.EntityField(be, FakeEmbedder(), "seat@host")
            f.write_events([ST.Event(stream="discourse", role="user", text=t, ts=10.0 + i)
                            for i, t in enumerate(texts)], session="s")
            f.process()
            st = {d["eid"]: np.asarray(d["state"]) for _, d in be.scan("entity_states", {"tier": "live"})}
            out.append(st)
            q = EF.unit(FakeEmbedder().embed(["gateway errors"])[0])
            with mock.patch.dict(os.environ, {"MEMBRANE_EF_ANN": "0"}):
                self.assertEqual(f.recall(q, k=1)[0][0], "w:gateway")
        self.assertEqual(out[0].keys(), out[1].keys())
        for k in out[0]:
            np.testing.assert_allclose(out[0][k], out[1][k], atol=1e-12)


class TestOpenBackend(unittest.TestCase):
    def test_lab_paths_and_the_env_force_ram(self):
        self.assertIsInstance(efstore.open_backend(os.path.join(tempfile.mkdtemp(), "f.json")),
                              MemBackend)
        with mock.patch.dict(os.environ, {"MEMBRANE_EFIELD_STORE": "mem"}):
            self.assertIsInstance(efstore.open_backend("/home/x/.membrane/field.json"), MemBackend)

    def test_an_unreachable_cluster_degrades_to_ram(self):
        with mock.patch("membrane.field.ExemplarStore._cb_collection",
                        side_effect=RuntimeError("down")):
            self.assertIsInstance(efstore.open_backend("/home/x/.membrane/field.json"), MemBackend)


if __name__ == "__main__":
    unittest.main()
