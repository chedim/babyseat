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
    from membrane.cblstore import CBLiteBackend, StoreBusy, dumps  # noqa: E402

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

    def test_a_drain_lets_a_waiting_turn_read_between_rows(self):
        """2026-10-31, profiled: the write-behind held ONE transaction (the store lock)
        over its whole batch; a turn's banner reads waited for all of it."""
        import threading
        b = self._be()
        b.upsert("sentences", "s0", {"text": "t"})
        b.drain()
        for i in range(400):
            b.upsert("entity_deltas", f"d{i}", {"origin": "o", "seq": i, "eid": "e"})
        real = b.db.put_json
        first, read_done, rows_after_read = threading.Event(), threading.Event(), []

        def put_json(*a, **k):
            r = real(*a, **k)
            if read_done.is_set():
                rows_after_read.append(1)
            first.set()
            return r
        b.db.put_json = put_json
        b._cache.clear()

        def turn():
            first.wait(5)
            with b.db.turns.lane():
                b.db.get_json("sentences", "s0")
            read_done.set()
        t = threading.Thread(target=turn)
        t.start()
        b.drain()
        t.join(5)
        self.assertTrue(read_done.is_set())
        self.assertTrue(rows_after_read, "the turn's read waited for the whole batch")

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


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestTheSwitchOver(unittest.TestCase):
    """The import runs while the old seat still writes its journals; the
    switch-over boot replays only what they gained since."""

    def test_the_tails_written_after_the_import_replay_at_the_switch(self):
        tmp = tempfile.mkdtemp()
        jdir = Path(tmp) / "field"
        f = FileBackend(jdir)
        for i in range(5):
            f.upsert("entity_states", f"e{i}", {"origin": "o", "mass": float(i)})
        f.insert_if_absent("sentences", "s0", {"_k": "sent", "text": "zero", "vec": f16(vecs(1, 2)[0])})
        f.flush()
        pre = CBLiteBackend(Path(tmp) / "field.cblite", journal_dir=jdir)      # the pre-import, seat still up
        pre.close()
        # the old seat keeps living
        f.upsert("entity_states", "e9", {"origin": "o", "mass": 9.0})
        f.upsert("entity_states", "e1", {"origin": "o", "mass": 1.5})
        f.delete("entity_states", "e3")
        f.insert_if_absent("sentences", "s1", {"_k": "sent", "text": "one", "vec": f16(vecs(1, 3)[0])})
        f.flush()
        want = list(FileBackend(jdir).scan("entity_states"))
        b = CBLiteBackend(Path(tmp) / "field.cblite", journal_dir=jdir)        # the switch-over boot
        try:
            self.assertEqual(list(b.scan("entity_states")), want)
            self.assertEqual(b.get("sentences", "s1")["text"], "one")
            self.assertEqual(b.replay_tails(jdir), {})                          # caught up: nothing twice
        finally:
            b.close()

    def test_a_journal_rewritten_since_replays_whole(self):
        tmp = tempfile.mkdtemp()
        jdir = Path(tmp) / "field"
        f = FileBackend(jdir)
        for i in range(6):
            f.upsert("entity_states", f"e{i}", {"origin": "o", "mass": float(i)})
        f.flush()
        CBLiteBackend(Path(tmp) / "field.cblite", journal_dir=jdir).close()
        f.delete("entity_states", "e2")
        f.delete("entity_states", "e4")
        jf = jdir / "entity_states.jsonl"
        rows = list(FileBackend(jdir).scan("entity_states"))
        jf.unlink()                                                            # a compaction: a new, shorter file
        jf.write_text("".join(json.dumps({"k": k, "d": d}) + "\n" for k, d in rows))
        b = CBLiteBackend(Path(tmp) / "field.cblite", journal_dir=jdir)
        try:
            self.assertEqual(sorted(k for k, _ in b.scan("entity_states")), sorted(k for k, _ in rows))
        finally:
            b.close()


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestLeanPreImport(unittest.TestCase):
    def test_the_lean_import_then_the_switch_boot(self):
        from membrane.cblstore import import_only
        tmp = tempfile.mkdtemp()
        jdir = Path(tmp) / "field"
        f = FileBackend(jdir)
        for i in range(4):
            f.upsert("events", f"ev{i}", {"origin": "o", "session": "s", "n": i})
        f.flush()
        self.assertEqual(import_only(Path(tmp) / "db", jdir)["events"], 4)
        self.assertEqual(import_only(Path(tmp) / "db", jdir), {})                # once
        f.upsert("events", "ev9", {"origin": "o", "session": "s", "n": 9})
        f.flush()
        b = CBLiteBackend(Path(tmp) / "db", journal_dir=jdir)
        try:
            self.assertEqual([k for k, _ in b.scan("events")], ["ev0", "ev1", "ev2", "ev3", "ev9"])
        finally:
            b.close()


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestWriteBehind(unittest.TestCase):
    def test_hot_writes_coalesce_and_survive_a_close(self):
        tmp = tempfile.mkdtemp()
        b = CBLiteBackend(Path(tmp) / "db")
        try:
            b._stop = True                       # hold the writer: the queue is visible
            b._wflush.set()
            b._writer.join(timeout=5)
            for i in range(50):
                b.upsert("entity_states", "e1", {"origin": "o", "mass": float(i)})
            b.upsert("entity_states", "e2", {"origin": "o", "mass": 2.0})
            b.delete("entity_states", "e2")
            self.assertEqual(len(b._pending), 2)                         # 51 writes -> 2 pending
            self.assertEqual(b.get("entity_states", "e1")["mass"], 49.0)  # RAM answers meanwhile
        finally:
            b.close()
        b = CBLiteBackend(Path(tmp) / "db")
        try:
            self.assertEqual(b.get("entity_states", "e1")["mass"], 49.0)
            self.assertIsNone(b.get("entity_states", "e2"))
        finally:
            b.close()

    def test_a_reused_dict_saves_what_ram_holds(self):
        tmp = tempfile.mkdtemp()
        b = CBLiteBackend(Path(tmp) / "db")
        d = {"origin": "o", "mass": 1.0}
        b.upsert("entity_states", "e1", d)
        d["mass"] = 99.0                                                 # the caller reuses its dict
        b.close()
        b = CBLiteBackend(Path(tmp) / "db")
        try:
            self.assertEqual(b.get("entity_states", "e1")["mass"], 1.0)
        finally:
            b.close()

@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestDrainFailures(unittest.TestCase):
    """issue #927: a busy-DB save must not lose the seat's write."""
    def _held(self):
        tmp = tempfile.mkdtemp()
        b = CBLiteBackend(Path(tmp) / "db")
        b._stop = True                           # hold the writer: drain by hand
        b._wflush.set()
        b._writer.join(timeout=5)
        return tmp, b

    def test_a_shutdown_that_cannot_save_records_and_tells(self):
        import io, json as _j
        from membrane import cblite as CB
        tmp, b = self._held()
        b.upsert("entity_states", "e9", {"origin": "o", "mass": 2.0})
        buf = io.StringIO()
        with mock.patch.object(b.db, "put_json", side_effect=CB.CBLException("busy")), \
                mock.patch("sys.stderr", buf):
            b.close()
        self.assertEqual(b.unsaved_at_close, ["entity_states/e9"])
        self.assertIn("UNSAVED", buf.getvalue())
        rec = _j.loads((Path(tmp) / "db.unsaved.json").read_text())
        self.assertEqual(rec["keys"], ["entity_states/e9"])

    def test_a_busy_save_requeues_the_failed_key_with_backoff(self):
        from membrane import cblite as CB
        tmp, b = self._held()
        try:
            b.upsert("entity_states", "e1", {"origin": "o", "mass": 1.0})
            with mock.patch.object(b.db, "put_json", side_effect=CB.CBLException("busy")):
                b.drain()
            self.assertIn(("entity_states", "e1"), b._pending)
            self.assertGreater(b._retry_at, 0)
            b._retry_at = 0
            b.drain()
            self.assertEqual(b._pending, {})
            self.assertEqual(b._retry_at, 0)
        finally:
            b.close()
        b = CBLiteBackend(Path(tmp) / "db")
        try:
            self.assertEqual(b.get("entity_states", "e1")["mass"], 1.0)
        finally:
            b.close()

    def _shut_unsaved(self, tmp, b, coll, key, doc):
        from membrane import cblite as CB
        b.upsert(coll, key, doc)
        with mock.patch.object(b.db, "put_json", side_effect=CB.CBLException("busy")), \
                mock.patch("sys.stderr", __import__("io").StringIO()):
            b.close()

    def test_the_record_keeps_the_values(self):
        import json as _j
        tmp, b = self._held()
        self._shut_unsaved(tmp, b, "entity_states", "e9", {"origin": "o", "mass": 2.0})
        rec = _j.loads((Path(tmp) / "db.unsaved.json").read_text())
        it = rec["items"][0]
        self.assertEqual((it["c"], it["k"], it["d"]["mass"]), ("entity_states", "e9", 2.0))
        self.assertIsNotNone(it["o"])

    def test_the_next_boot_reapplies_the_unsaved_values(self):
        tmp, b = self._held()
        self._shut_unsaved(tmp, b, "entity_states", "e9", {"origin": "o", "mass": 2.0})
        b2 = CBLiteBackend(Path(tmp) / "db")
        try:
            self.assertEqual(b2.get("entity_states", "e9")["mass"], 2.0)
            self.assertEqual(b2.recovered_at_boot["applied"], 1)
        finally:
            b2.close()

    def test_a_newer_row_on_disk_beats_the_record(self):
        import json as _j
        tmp, b = self._held()
        self._shut_unsaved(tmp, b, "entity_states", "e9", {"origin": "o", "mass": 2.0})
        rp = Path(tmp) / "db.unsaved.json"
        rec = _j.loads(rp.read_text())
        old_o = rec["items"][0]["o"]
        b0 = CBLiteBackend(Path(tmp) / "db")          # boot 1 applies it ...
        b0.close()
        rec["items"][0]["o"] = old_o - 1              # ... a record older than the disk row
        rec["items"][0]["d"]["mass"] = 99.0
        rp.write_text(_j.dumps(rec))
        b2 = CBLiteBackend(Path(tmp) / "db")
        try:
            self.assertEqual(b2.get("entity_states", "e9")["mass"], 2.0)
            self.assertEqual(b2.recovered_at_boot["stale"], 1)
        finally:
            b2.close()

    def test_a_newer_write_beats_the_requeued_one(self):
        from membrane import cblite as CB
        tmp, b = self._held()
        try:
            b.upsert("entity_states", "e1", {"origin": "o", "mass": 1.0})

            def fail_after_newer(*a, **k):
                b.upsert("entity_states", "e1", {"origin": "o", "mass": 2.0})
                raise CB.CBLException("busy")
            with mock.patch.object(b.db, "put_json", side_effect=fail_after_newer):
                b.drain()
            self.assertEqual(b._pending[("entity_states", "e1")][1]["mass"], 2.0)
        finally:
            b.close()

    def test_begin_and_purge_failures_do_not_lose_the_batch(self):
        from membrane import cblite as CB
        tmp, b = self._held()
        try:
            b.upsert("entity_states", "e1", {"origin": "o", "mass": 1.0})
            b.drain()
            b.upsert("entity_states", "e2", {"origin": "o", "mass": 2.0})
            b.delete("entity_states", "e1")
            with mock.patch.object(b.db, "begin", side_effect=CB.CBLException("busy")):
                b.drain()
            self.assertEqual(set(b._pending), {("entity_states", "e1"), ("entity_states", "e2")})
            b._retry_at = 0
            with mock.patch.object(b.db, "purge", side_effect=CB.CBLException("busy")):
                b.drain()
            self.assertEqual(set(b._pending), {("entity_states", "e1")})   # e2 saved; the failed purge stays
            b._retry_at = 0
            b.drain()
            self.assertEqual(b._pending, {})
        finally:
            b.close()
        b = CBLiteBackend(Path(tmp) / "db")
        try:
            self.assertIsNone(b.get("entity_states", "e1"))
            self.assertEqual(b.get("entity_states", "e2")["mass"], 2.0)
        finally:
            b.close()


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestWriterLockAndEmptyKeys(unittest.TestCase):
    """issues #26 + #11 on the cblite home."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_a_second_backend_on_a_live_store_fails_fast(self):
        # issue #26: `membrane.backfill --audit` beside a live seat opened
        # fine (SQLite storage) and crashed LATER mid-replay
        # ('save: database busy/locked'), leaving a half-rebuilt field --
        # now every constructor takes a sidecar writer flock
        b = CBLiteBackend(Path(self.tmp) / "field.cblite")
        try:
            with self.assertRaises(StoreBusy):
                CBLiteBackend(Path(self.tmp) / "field.cblite")
        finally:
            b.close()
        again = CBLiteBackend(Path(self.tmp) / "field.cblite")   # released with close
        again.close()

    def test_an_empty_doc_id_matches_nothing(self):
        # issue #11: the native layer warns per call on the document id ''
        # (night's singular `d.get('sid')`, always None); the binding now
        # answers None without reaching it
        b = CBLiteBackend(Path(self.tmp) / "field.cblite")
        try:
            self.assertIsNone(b.get("sentences", None))
            self.assertIsNone(b.get("sentences", ""))
            self.assertIsNone(b.db.get_json("sentences", ""))
            self.assertFalse(b.db.exists("sentences", ""))
            self.assertFalse(b.db.exists("sentences", None))
        finally:
            b.close()
