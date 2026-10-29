"""event_by_number reads the events' scan index, not every row: the banner's
speaker step called it once per pool row, 82 ms each on a 318k-row seat (#668)."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import efstore  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


class TestEventByNumber(unittest.TestCase):
    def test_reads_the_index(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        f.write_events([ST.Event(stream="discourse", role="user", ts=1_800_000_000.0 + i,
                                 text=f"note number {i} about the bilge pump")
                        for i in range(200)], session="s:1:a")
        f.process()
        key, d = f.event_by_number(150)
        real, seen = efstore.matches, []

        def spy(d, eq):
            seen.append(1)
            return real(d, eq)

        with mock.patch.object(efstore, "matches", spy):
            got = f.event_by_number(150)
        self.assertEqual(got[0], key)
        self.assertLessEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
