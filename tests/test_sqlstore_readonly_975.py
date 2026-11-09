"""PR 975 send-back item 1: a readonly SQLite store searches the existing vector file, overlays RAM rows
with the right row indexes, and never writes."""
import base64
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.sqlstore import SQLiteBackend as B  # noqa: E402


def vec(s):
    v = np.random.default_rng(s).standard_normal(1024).astype(np.float32)
    return v / np.linalg.norm(v)


def pack(v):
    return "f16:" + base64.b64encode(v.astype(np.float16).tobytes()).decode()


def digests(root):
    return {str(p.relative_to(root)): hashlib.sha1(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file() and "-wal" not in p.name
            and "-shm" not in p.name}


def _seed(path):
    be = B(path)
    for i, n in enumerate("abc"):
        be.upsert("sentences", n, {"text": n, "vec": pack(vec(i + 1))})
    be.flush()
    return be


class ReadonlySearch(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.p = str(Path(self.d) / "db")

    def test_readonly_finds_rows_already_in_the_file(self):
        be = _seed(self.p)
        be.catch_up()
        be.close()
        ro = B(self.p, readonly=True)
        try:
            top = ro.vector_top_k("sentences", "vec", vec(1), 2)
        finally:
            ro.close()
        self.assertTrue(top and top[0][0] == "a" and top[0][1] > 0.99, top)

    def test_readonly_new_row_overlay_has_the_right_key_and_old_rows_still_found(self):
        be = _seed(self.p)
        be.catch_up()
        be.vector_top_k("sentences", "vec", vec(1), 1)
        be.upsert("sentences", "new", {"text": "n", "vec": pack(vec(50))})
        be.flush()
        with mock.patch.object(B, "catch_up", lambda s, *a, **k: 0):
            be.close()
        ro = B(self.p, readonly=True)
        try:
            a = ro.vector_top_k("sentences", "vec", vec(1), 2)
            new = ro.vector_top_k("sentences", "vec", vec(50), 2)
        finally:
            ro.close()
        self.assertTrue(a and a[0][0] == "a" and a[0][1] > 0.99, a)
        self.assertTrue(new and new[0][0] == "new" and new[0][1] > 0.99, new)

    def test_readonly_never_writes(self):
        be = _seed(self.p)
        be.catch_up()
        be.close()
        before = digests(self.p)
        ro = B(self.p, readonly=True)
        try:
            ro.vector_top_k("sentences", "vec", vec(1), 2)
        finally:
            ro.close()
        self.assertEqual(before, digests(self.p))


if __name__ == "__main__":
    unittest.main()
