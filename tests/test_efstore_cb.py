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

    def test_the_bucket_ladder_prefers_a_cortex_bucket(self):
        from membrane.substrate import reuse_choice
        self.assertEqual(reuse_choice(["alpha", "cortex-ephemeral", "cortex-core"]), "cortex-core")
        self.assertEqual(reuse_choice(["beta", "alpha"]), "beta")
        self.assertIsNone(reuse_choice([]))

    def test_an_unreachable_cluster_degrades_to_file(self):
        """THE SEAT-FIRST FLIP (stage 4): the couchbase field answers
        only behind MEMBRANE_FIELD_CLOUD=1 now -- and a down cluster
        degrades to the FILE home under the seat's folder, never to RAM
        (RAM forgets on restart; the file survives)."""
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "rag-field.json")
            with mock.patch.dict(os.environ, {"MEMBRANE_FIELD_CLOUD": "1"}), \
                    mock.patch("membrane.substrate.cb_open",
                               side_effect=RuntimeError("down")), \
                    mock.patch("membrane.substrate._is_lab_field_path",
                               return_value=False):
                self.assertIsInstance(efstore.open_backend(fp),
                                      efstore.FileBackend)


if __name__ == "__main__":
    unittest.main()


from membrane.efstore import count_sql  # noqa: E402


class TestCounts(unittest.TestCase):
    def test_count_sql_pure(self):
        sql, p = count_sql("b", "membrane", "events",
                           {"origin": "o"}, group=("stream", "status"))
        self.assertEqual(
            sql,
            "SELECT t.`stream` AS `stream`, t.`status` AS `status`, "
            "COUNT(*) AS `n` FROM `b`.`membrane`.`events` t "
            "WHERE t.`origin` = $p0 GROUP BY t.`stream`, t.`status`")
        self.assertEqual(p, {"p0": "o"})
        sql2, p2 = count_sql("b", "membrane", "events", None)
        self.assertEqual(sql2, "SELECT COUNT(*) AS `n` FROM `b`.`membrane`.`events` t")
        self.assertEqual(p2, {})

    def test_count_groups_backend_parity(self):
        be = MemBackend()
        be.upsert("events", "a", {"origin": "o", "stream": "discourse", "status": "user"})
        be.upsert("events", "b", {"origin": "o", "stream": "discourse", "status": "user"})
        be.upsert("events", "c", {"origin": "o", "stream": "tool", "status": "result"})
        be.upsert("events", "d", {"origin": "other", "stream": "discourse", "status": "user"})
        mem = be.count("events", {"origin": "o"}, group=("stream", "status"))
        self.assertEqual(sorted((r["stream"], r["status"], r["n"]) for r in mem),
                         [("discourse", "user", 2), ("tool", "result", 1)])
        self.assertEqual(be.count("events"), {"n": 4})
        cbbe = cb()
        cbbe.upsert("events", "a", {"origin": "o", "stream": "discourse", "status": "user"})
        cbbe.upsert("events", "b", {"origin": "o", "stream": "discourse", "status": "user"})
        cbbe.upsert("events", "c", {"origin": "o", "stream": "tool", "status": "result"})
        cbbe.upsert("events", "d", {"origin": "other", "stream": "discourse", "status": "user"})
        remote = cbbe.count("events", {"origin": "o"}, group=("stream", "status"))
        self.assertEqual(sorted((r["stream"], r["status"], r["n"]) for r in remote),
                         [("discourse", "user", 2), ("tool", "result", 1)])
        self.assertEqual(cbbe.count("events"), {"n": 4})

    def test_scan_desc_order_top_m(self):
        for be in (MemBackend(), cb()):
            be.upsert("entity_states", "a", {"origin": "o", "tier": "live", "accr": 3})
            be.upsert("entity_states", "b", {"origin": "o", "tier": "live", "accr": 9})
            be.upsert("entity_states", "c", {"origin": "o", "tier": "live", "accr": 6})
            keys = [k for k, d in be.scan(
                "entity_states", {"origin": "o", "tier": "live"},
                order=(("accr", "DESC"),), limit=2)]
            self.assertEqual(keys, ["b", "c"])
            keys = [k for k, d in be.scan(
                "entity_states", {"origin": "o", "tier": "live"},
                order=(("accr", "DESC"),))]
            self.assertEqual(keys, ["b", "c", "a"])

    def test_vitals_counts_without_doc_scans(self):
        from membrane.efield import EntityField
        be = MemBackend()
        fld = EntityField(be, FakeEmbedder(), "o")
        fld.write_events([
            ST.Event(stream="discourse", role="user", text="the router picks models", ts=5.0),
            ST.Event(stream="observation", role="tool", tool="edit", ts=6.0),
        ], session="s:0:0")
        fld.b.upsert("entity_states", "x", {"origin": "o", "tier": "live",
                                            "accr": 5, "state": list(FakeEmbedder().embed(("a b",))[0])})
        seen = []
        real = fld.b

        class Seen:
            def __getattr__(self, name):
                return getattr(real, name)

            def count(self, *a, **kw):
                seen.append("count")
                return real.count(*a, **kw)

            def scan(self, coll=None, *a, **kw):
                seen.append(f"scan {coll}")
                return real.scan(coll, *a, **kw)

        fld.b = Seen()
        try:
            v = fld.vitals(max_age_s=0)
        finally:
            fld.b = real
        self.assertEqual(sorted(v["events"]),
                         ["discourse/pending", "observation/pending"])
        self.assertEqual(v["entities"], {"live": 1})
        self.assertEqual(v["events"]["discourse/pending"], 1)
        self.assertTrue(seen, "vitals made no backend calls")
        self.assertEqual(seen[:2], ["count", "count"])
        # geometry's top-m states pull may scan (ordered, bounded); the
        # events collection must NEVER be doc-scanned for a count
        self.assertTrue(all(s != "scan events" for s in seen))
