"""entity_deltas' sid IN-list scan (events_of_sentences, inside every
sentence search) reads the scan index, not every delta: ~0.5 s a call on a
318k-row seat (#668). Same rows, same order (the collection's own)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efstore  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402


def filled():
    b = MemBackend()
    for i in range(300):
        b.upsert("entity_deltas", f"d{i}", {"origin": "o", "seq": i, "sid": f"s{i % 50}", "event": f"e{i}"})
    return b


class TestSidIndex(unittest.TestCase):
    def test_in_list_reads_the_index(self):
        b = filled()
        list(b.scan("entity_deltas", {"origin": "o", "sid": ["s1"]}))   # builds the index
        real, seen = efstore.matches, []

        def spy(d, eq):
            seen.append(1)
            return real(d, eq)

        with mock.patch.object(efstore, "matches", spy):
            rows = list(b.scan("entity_deltas", {"origin": "o", "sid": ["s3", "s7"]}))
        self.assertEqual(len(rows), 12)
        self.assertLessEqual(len(seen), 12)

    def test_same_rows_same_order_as_a_full_scan(self):
        b = filled()
        b.upsert("entity_deltas", "d5", {"origin": "o", "seq": 5, "sid": "s9", "event": "moved"})
        want = [(k, d) for k, d in b.colls["entity_deltas"].items()
                if d["sid"] in {"s9", "s3", "s49"}]
        got = list(b.scan("entity_deltas", {"origin": "o", "sid": ["s49", "s3", "s9"]}))
        self.assertEqual(got, want)
        b.upsert("entity_deltas", "d900", {"origin": "o", "seq": 900, "sid": "s3", "event": "new"})
        got = list(b.scan("entity_deltas", {"origin": "o", "sid": ["s3", "s9"]}))
        self.assertEqual([k for k, _ in got],
                         [k for k, d in b.colls["entity_deltas"].items() if d["sid"] in {"s3", "s9"}])


if __name__ == "__main__":
    unittest.main()
