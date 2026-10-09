"""THE INDEX THAT NEVER GOT BUILT (2026-10-08, red first).

Since fdee162 (the STALE-OK fix: a request thread never builds) a big
collection (> 2000 rows) with NO matrix yet answered empty and scheduled a
background rebuild -- but the rebuild only refreshed matrices that already
EXISTED. A never-built one stayed missing forever: on the live seat every
sentence search (recall, events_near, the massage) found nothing.

Pinned: the first search of a big collection still answers empty (the
request thread never builds) and the background build makes the next
search hit; small worlds keep building inline; a write still refreshes.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.efstore import FileBackend, MemBackend, pack_f16  # noqa: E402


def _fill(b, n, d=32, seed=0):
    rng = np.random.default_rng(seed)
    for i in range(n):
        v = rng.normal(size=d)
        b.upsert("sentences", f"s{i}", {"text": f"t{i}", "vec": pack_f16(v / np.linalg.norm(v))})
    return rng


def _wait_hits(b, q, k=5, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        hits = b.vector_top_k("sentences", "vec", q, k)
        if hits:
            return hits
        time.sleep(0.05)
    return []


class TestFirstBuild(unittest.TestCase):
    def test_a_big_collection_gets_its_first_matrix(self):
        b = MemBackend()
        _fill(b, 3000)
        q = np.ones(32)
        first = b.vector_top_k("sentences", "vec", q, 5)
        self.assertEqual(first, [])                 # the request thread never builds
        self.assertEqual(len(_wait_hits(b, q)), 5)  # ...the background one does

    def test_a_small_world_builds_inline(self):
        b = MemBackend()
        _fill(b, 200)
        self.assertEqual(len(b.vector_top_k("sentences", "vec", np.ones(32), 5)), 5)

    def test_the_seats_file_backend_gets_its_first_matrix(self):
        b = FileBackend(Path(tempfile.mkdtemp()) / "field")
        _fill(b, 3000)
        self.assertEqual(len(_wait_hits(b, np.ones(32))), 5)

    def test_a_write_still_refreshes(self):
        # the seat's backend: its writes mark the matrix dirty (MemBackend's
        # bare upsert never did -- the lab store)
        b = FileBackend(Path(tempfile.mkdtemp()) / "field")
        _fill(b, 3000)
        q = np.ones(32)
        _wait_hits(b, q)
        b.upsert("sentences", "target", {"text": "x", "vec": pack_f16(q / np.linalg.norm(q))})
        end = time.time() + 20
        hits = []
        while time.time() < end:
            hits = b.vector_top_k("sentences", "vec", q, 1)
            if hits and hits[0][0] == "target":
                break
            time.sleep(0.05)
        self.assertEqual(hits[0][0], "target")


if __name__ == "__main__":
    unittest.main()
