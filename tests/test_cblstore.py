"""THE COUCHBASE LITE HOME (2026-10-10): the field's store on CBL wherever
the libraries are vendored (scripts/fetch-cbl.sh). The hot collections keep
the RAM home's semantics exactly (order included) with CBL as their
persistence; sentences live only in CBL behind a lazy vector index; the old
JSONL journals import once. Skipped where the libraries aren't fetched."""
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane.efstore import FileBackend, MemBackend  # noqa: E402

HAVE = cblite.lib_paths() is not None
if HAVE:
    from membrane.cblstore import CBLiteBackend, dumps  # noqa: E402

DIM = 1024


def f16(v):
    return "f16:" + base64.b64encode(np.asarray(v, np.float16).tobytes()).decode()


def vecs(n, seed=1):
    r = np.random.default_rng(seed)
    c = r.standard_normal((20, DIM)).astype(np.float32)
    a = c[r.integers(0, 20, n)] + 0.7 * r.standard_normal((n, DIM)).astype(np.float32)
    return a / np.linalg.norm(a, axis=1, keepdims=True)


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestCBLiteBackend(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.open = []

    def tearDown(self):
        for b in self.open:
            try:
                b.close()
            except Exception:
                pass

    def _be(self, journal_dir=None):
        b = CBLiteBackend(Path(self.tmp) / "field.cblite", journal_dir=journal_dir)
        self.open.append(b)
        return b

    def _reopen(self, b):
        b.close()
        self.open.remove(b)
        return self._be()

    def test_hot_rows_survive_a_reopen_in_their_first_written_order(self):
        b = self._be()
        for i in (3, 1, 2):
            b.upsert("entity_deltas", f"d{i}", {"origin": "o", "seq": i, "eid": "e"})
        b.upsert("entity_deltas", "d3", {"origin": "o", "seq": 3, "eid": "e2"})    # an update keeps its place
        b.delete("entity_deltas", "d1")
        b.upsert("entity_deltas", "d1", {"origin": "o", "seq": 1, "eid": "e"})     # a re-add moves to the end
        before = list(b.scan("entity_deltas"))
        b = self._reopen(b)
        self.assertEqual(list(b.scan("entity_deltas")), before)
        self.assertEqual([k for k, _ in before], ["d3", "d2", "d1"])
        self.assertEqual(b.get("entity_deltas", "d3")["eid"], "e2")
        self.assertEqual([k for k, _ in b.scan("entity_deltas", {"seq": {"gt": 1}})], ["d3", "d2"])

    def test_the_hot_contract_is_the_ram_homes(self):
        b, m = self._be(), MemBackend()
        for be in (b, m):
            be.upsert_multi("events", {f"e{i}": {"origin": "o", "session": f"s{i % 3}", "n": i, "status": "applied"}
                                       for i in range(30)})
            be.cas_update("events", "e4", lambda d: dict(d, status="served"))
            be.cas_update("events", "e5", lambda d: None)
            self.assertTrue(be.insert_if_absent("claims", "c1", {"x": 1}))
            self.assertFalse(be.insert_if_absent("claims", "c1", {"x": 2}))
        for q in ({"session": "s1"}, {"status": "served"}, {"n": {"gte": 25}}, {"session": ["s0", "s2"]}):
            self.assertEqual(list(b.scan("events", q, order=("n",))), list(m.scan("events", q, order=("n",))), q)
        self.assertEqual(b.count("events", group=("session",)), m.count("events", group=("session",)))
        self.assertEqual(b.get("claims", "c1"), {"x": 1})

    def test_sentences_live_in_cbl_and_are_found_by_their_vectors(self):
        b = self._be()
        V = vecs(400)
        b.upsert_multi("sentences", {f"s{i}": {"_k": "sent", "text": f"t{i}", "vec": f16(v)} for i, v in enumerate(V)})
        self.assertEqual(b.colls["sentences"], {})                   # not in RAM
        self.assertTrue(b.insert_if_absent("sentences", "new", {"_k": "sent", "text": "new", "vec": f16(V[7])}))
        self.assertFalse(b.insert_if_absent("sentences", "new", {"text": "dup"}))
        b.catch_up()
        hits = b.vector_top_k("sentences", "vec", V[7], 5)
        self.assertEqual({k for k, _ in hits[:2]}, {"s7", "new"})
        self.assertGreater(hits[0][1], 0.95)                          # a similarity, like the RAM home's
        self.assertEqual(b.get_multi("sentences", ["s3", "nope"])["s3"]["text"], "t3")
        self.assertEqual(b.count("sentences"), {"n": 401})
        self.assertEqual(len(list(b.scan("sentences"))), 401)        # the move tool's full walk
        b = self._reopen(b)
        b.catch_up()
        self.assertEqual(b.vector_top_k("sentences", "vec", V[9], 1)[0][0], "s9")

    def test_the_journals_import_once_and_the_ram_dict_order_holds(self):
        jdir = Path(self.tmp) / "field"
        f = FileBackend(jdir)
        V = vecs(30, 5)
        for i, v in enumerate(V):
            f.insert_if_absent("sentences", f"s{i}", {"_k": "sent", "text": f"t{i}", "vec": f16(v)})
        for i in range(10):
            f.upsert("entity_states", f"e{i}", {"origin": "o", "tier": "live", "kind": "Device", "mass": float(i)})
        f.upsert("entity_states", "e2", {"origin": "o", "tier": "live", "kind": "Person", "mass": 2.5})
        f.delete("entity_states", "e4")
        f.upsert("events", "ev1", {"origin": "o", "session": "x", "n": 1, "status": "pending"})
        f.upsert("events", "ev1", {"origin": "o", "session": "x", "n": 1, "status": "applied"})
        f.close()
        want = {c: list(FileBackend(jdir).scan(c)) for c in ("entity_states", "events")}
        b = self._be(journal_dir=jdir)
        for c, rows in want.items():
            self.assertEqual(list(b.scan(c)), rows, c)
        self.assertEqual(b.get("sentences", "s3")["text"], "t3")
        self.assertIsNone(b.get("entity_states", "e4"))
        b.catch_up()
        self.assertEqual(b.vector_top_k("sentences", "vec", V[12], 1)[0][0], "s12")
        # a second boot never re-imports (the journals are a backup now)
        (jdir / "entity_states.jsonl").write_text(json.dumps({"k": "zzz", "d": {"origin": "o"}}) + "\n")
        b = self._reopen(b)
        self.assertIsNone(b.get("entity_states", "zzz"))
        self.assertEqual(list(b.scan("entity_states")), want["entity_states"])

    def test_a_poison_row_still_writes(self):
        self.assertEqual(json.loads(dumps({"a": float("nan"), "b": [float("inf"), 1.0]})), {"a": None, "b": [None, 1.0]})
        b = self._be()
        b.upsert("annotations", "a1", {"x": float("nan"), "f": lambda: 1})
        b = self._reopen(b)
        self.assertIsNone(b.get("annotations", "a1")["x"])

    def test_the_hot_vector_path_is_the_ram_homes(self):
        b = self._be()
        V = vecs(50, 9)
        b.upsert_multi("entity_states", {f"e{i}": {"origin": "o", "tier": "live", "kind": "Device",
                                                   "state": f16(v)} for i, v in enumerate(V)})
        import time
        for _ in range(100):                                        # the RAM home builds its matrix in the background
            hits = b.vector_top_k("entity_states", "state", V[4], 3, eq={"tier": "live"})
            if hits:
                break
            time.sleep(0.05)
        self.assertEqual(hits[0][0], "e4")


class TestTheLadder(unittest.TestCase):
    def test_vendored_libraries_make_it_the_default_home(self):
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "rag-field.json")
            with mock.patch("membrane.substrate._is_lab_field_path", return_value=False), \
                    mock.patch("membrane.efstore._cbl_wanted", return_value=False):
                self.assertIsInstance(efstore.open_backend(fp), FileBackend)
            with mock.patch.dict(os.environ, {"MEMBRANE_EFIELD_STORE": "file"}), \
                    mock.patch("membrane.substrate._is_lab_field_path", return_value=False):
                self.assertIsInstance(efstore.open_backend(fp), FileBackend)
            if HAVE:
                with mock.patch("membrane.substrate._is_lab_field_path", return_value=False):
                    be = efstore.open_backend(fp)
                try:
                    self.assertIsInstance(be, CBLiteBackend)
                    self.assertEqual(be.path, (Path(d) / ".membrane" / "field.cblite").resolve())
                finally:
                    be.close()

    def test_the_ram_guard_still_wins(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_EFIELD_STORE": "mem"}):
            self.assertIsInstance(efstore.open_backend(os.path.join(tempfile.mkdtemp(), "rag-field.json")), MemBackend)


if __name__ == "__main__":
    unittest.main()
