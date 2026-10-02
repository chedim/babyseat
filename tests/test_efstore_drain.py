"""The crash-covenant drain pin (docs/crash-covenant.md):
CouchbaseBackend.count() fully drains the lazy QueryResult -- the
couchbase 4.6.3 destroyed-unawaited-iterator shape that SIGABRTs the seat
-- and returns {"n": 0} on an empty stream instead of StopIteration."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.efstore import CouchbaseBackend


class _FakeStream:
    def __init__(self, rows):
        self._rows = list(rows)
        self.drained = False

    def __iter__(self):
        for r in self._rows:
            yield r
        self.drained = True


def _mk(rows):
    b = object.__new__(CouchbaseBackend)   # no cluster, no connection
    b.bucket_name, b.scope_name = "test_bucket", "test_scope"
    stream = _FakeStream(rows)
    b._query = lambda sql, params=None: stream
    return b, stream


class TestCountDrains(unittest.TestCase):
    def test_count_drains_fully(self):
        b, s = _mk([{"n": 7}])
        self.assertEqual(b.count("events"), {"n": 7})
        self.assertTrue(s.drained)

    def test_count_empty_returns_zero(self):
        b, s = _mk([])
        self.assertEqual(b.count("events"), {"n": 0})
        self.assertTrue(s.drained)

    def test_count_group_drains(self):
        b, s = _mk([{"n": 1, "origin": "a"}, {"n": 2, "origin": "b"}])
        self.assertEqual(b.count("events", group=("origin",)),
                         [{"n": 1, "origin": "a"}, {"n": 2, "origin": "b"}])
        self.assertTrue(s.drained)


if __name__ == "__main__":
    unittest.main()
