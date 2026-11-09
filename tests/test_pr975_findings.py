"""PR 975 review findings, one failing test each on head 164a0a2d."""
import base64
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane import storekind as SK  # noqa: E402
from membrane.sqlstore import SQLiteBackend  # noqa: E402


def vec(seed):
    v = np.random.default_rng(seed).standard_normal(1024).astype(np.float32)
    return v / np.linalg.norm(v)


def pack(v):
    return "f16:" + base64.b64encode(v.astype(np.float16).tobytes()).decode()


def open_be(d, **kw):
    return SQLiteBackend(str(Path(d) / "db"), **kw)


class Findings(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = self.t.name

    def tearDown(self):
        self.t.cleanup()

    def test1_deleted_vector_stays_gone_after_reopen(self):
        be = open_be(self.d)
        be.upsert("sentences", "a", {"text": "a", "vec": pack(vec(1))})
        be.upsert("sentences", "b", {"text": "b", "vec": pack(vec(2))})
        be.flush(); be.vector_top_k("sentences", "vec", vec(1), 2); be.catch_up()
        be.delete("sentences", "a")
        for i in range(3):                       # more new rows than the deleted one: rows >= DB again
            be.upsert("sentences", f"c{i}", {"text": "c", "vec": pack(vec(3 + i))})
        be.flush()
        with mock.patch.object(SQLiteBackend, "catch_up", lambda self, *a, **k: 0):
            be.close()                          # new sentences are in the DB, not yet in the matrix
        be = open_be(self.d)
        keys = [k for k, _ in be.vector_top_k("sentences", "vec", vec(1), 5)]
        self.assertNotIn("a", keys)

    def test1_replaced_vector_not_duplicated_after_reopen(self):
        be = open_be(self.d)
        be.upsert("sentences", "a", {"text": "a", "vec": pack(vec(1))})
        be.flush(); be.vector_top_k("sentences", "vec", vec(1), 2)
        be.upsert("sentences", "a", {"text": "a2", "vec": pack(vec(9))})
        be.flush(); be.catch_up(); be.close()
        be = open_be(self.d)
        top = be.vector_top_k("sentences", "vec", vec(1), 5)
        self.assertEqual([k for k, _ in top].count("a"), 1)
        self.assertLess(dict(top).get("a", 0), 0.99)

    def test2_readonly_open_does_not_write_vector_file(self):
        be = open_be(self.d)
        be.upsert("sentences", "a", {"text": "a", "vec": pack(vec(1))})
        be.flush()
        with mock.patch.object(SQLiteBackend, "catch_up", lambda self, *a, **k: 0):
            be.close()                          # a live seat that has not seeded its matrix yet
        vd = Path(self.d) / "db" / "vectors"
        snap = lambda: {p.name: p.stat().st_size for p in vd.glob("*")} if vd.exists() else {}
        before = snap()
        ro = open_be(self.d, readonly=True)
        ro.vector_top_k("sentences", "vec", vec(1), 1)
        self.assertEqual(before, snap())

    def test3_keys_does_not_swallow_query_errors(self):
        class Bad:
            def __getattr__(self, n):
                def f(*a, **k):
                    raise RuntimeError("x")
                return f
        with self.assertRaises(Exception):
            SK._keys(Bad(), "events")

    def test_catch_up_seed_does_not_block_write_or_search(self):
        import threading
        be = open_be(self.d)
        for i in range(3):
            be.upsert("sentences", f"s{i}", {"text": "x", "vec": pack(vec(i))})
        be.flush()
        be.close()
        be = open_be(self.d)
        inside, release = threading.Event(), threading.Event()
        real = be.db.iter_query

        def slow(*a, **k):
            rows = list(real(*a, **k))     # the raw read is done (db lock freed); the scan is held here
            inside.set()
            release.wait(10)
            yield from rows
        be.db.iter_query = slow
        seeder = threading.Thread(target=be.catch_up)
        seeder.start()
        self.assertTrue(inside.wait(5))
        done = []

        def work():
            be.upsert("sentences", "late", {"text": "l", "vec": pack(vec(99))})
            be.vector_top_k("sentences", "vec", vec(1), 2)
            done.append(1)
        w = threading.Thread(target=work)
        w.start()
        w.join(3)
        stuck = w.is_alive()
        release.set()
        seeder.join(10)
        w.join(10)
        self.assertFalse(stuck, "write/search blocked behind the seed")
        be.db.iter_query = real
        be.flush(); be.catch_up()
        mx = be._vectors()
        self.assertEqual(len(mx.keys), len(set(mx.keys)), "duplicate rows")
        self.assertIn("late", mx.keys)
        self.assertEqual(len(mx.keys), 4)

    def test4_concurrent_catch_up_adds_no_duplicate_rows(self):
        import threading
        be = open_be(self.d)
        for i in range(200):
            be.upsert("sentences", f"s{i}", {"text": "a", "vec": pack(vec(i))})
        be.flush()
        ts = [threading.Thread(target=be.catch_up) for _ in range(4)]
        [t.start() for t in ts]; [t.join() for t in ts]
        mx = be._vectors()
        self.assertEqual(len(mx.keys), len(set(mx.keys)))

    def test5_keys_file_is_append_only(self):
        be = open_be(self.d)
        for i in range(3):
            be.upsert("sentences", f"s{i}", {"text": "t", "vec": pack(vec(i))})
        be.flush(); be.catch_up()
        kf = be._vectors().kf
        first = kf.read_bytes()
        be.upsert("sentences", "s9", {"text": "t", "vec": pack(vec(9))})
        be.flush(); be.catch_up()
        self.assertTrue(kf.read_bytes().startswith(first), "keys file was rewritten, not appended")

    def test6_eq_filter_returns_k_when_matches_exist(self):
        be = open_be(self.d)
        q = vec(0)
        for i in range(30):
            be.upsert("sentences", f"n{i}", {"text": "x", "origin": "no", "vec": pack(q + 0.001 * vec(100 + i))})
        for i in range(3):
            be.upsert("sentences", f"y{i}", {"text": "x", "origin": "yes", "vec": pack(vec(500 + i))})
        be.flush()
        top = be.vector_top_k("sentences", "vec", q, 3, eq={"origin": "yes"})
        self.assertEqual(len(top), 3)

    def test7_seat_with_only_journal_files_is_not_new(self):
        f = str(Path(self.d) / "field")
        base, jdir, sdir, cdir = SK.dirs(f)
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "events-0001.jsonl").write_text('{"x":1}\n')
        self.assertFalse(SK.is_new(f))


if __name__ == "__main__":
    unittest.main()
