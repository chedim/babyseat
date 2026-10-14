"""THE SEEDLESS BACKLOG (issue #33): notes minted pre-7c77d8d never gained
sids -- their event rows stand at status "empty", no sentence to embed,
apply never sees them, no state forms, and recall misses every one. The
fix (7c77d8d) repaired the MINT side; the stored rows stood still (note
40418: minted 10-12, still nursery, seed none, unresolvable). This pins
the migration: a field whose journal holds pre-fix-shaped rows recovers
them -- recovered rows return to pending, the ordinary embed/apply flow
embeds their recovered sentences, forms the EntityState, and the canary
becomes retrievable. A row the fixed splitter still finds nothing for
stays put, honestly, and the sweep self-empties (a second pass recovers
nothing).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

# the canary's shape: short prose, no terminal punctuation -- the splitter
# (pre-7c77d8d) returned [] for it; a specific subject (the script-tell
# lane), so one applied mention materializes it (the one-row bar)
NOTE = "fact one on the membrane field"
SUBJECT = "f:pkg/gw.py"


class TestTheSeedlessBacklog(unittest.TestCase):
    def _field(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        return f

    def _prefix_row(self, f, text, ts):
        """Mint a row the PRE-fix seat would have: written pending, then
        the pre-7c77d8d embed pass found no sentences (the old splitter's
        length gate) and left it 'empty' with no sids."""
        key = f.write_events([ST.Event(stream="discourse", role="note",
                                       text=text, ts=ts, subject=SUBJECT)],
                             session="s1")[0]
        d = f.b.get("events", key)
        self.assertEqual(d["status"], "pending")
        d.update({"status": "empty", "sids": [], "mentions": [],
                  "speaker": None})
        f.b.upsert("events", key, d)
        return key

    def test_a_pre_fix_note_graduates_post_migration(self):
        f = self._field()
        self._prefix_row(f, NOTE, 1000.0)
        key = list(f.b.scan("events"))[0][0]
        d = f.b.get("events", key)
        self.assertEqual(d["status"], "empty")   # the pre-fix shape, in hand
        # the honest read on the unfixed field: no state, nothing to recall
        self.assertIsNone(f.get(SUBJECT))
        n = f.backfill_seedless()
        self.assertGreaterEqual(n, 1)
        d = f.b.get("events", key)
        self.assertEqual(d["status"], "applied")
        self.assertTrue(d.get("sids"))
        for sid in d["sids"]:                     # the recovered sentences embed
            self.assertIn(sid, f.b.get_multi("sentences", d["sids"]))
        st = f.get(SUBJECT)
        self.assertIsNotNone(st, "the EntityState never formed")
        st2 = EF.EntityField(f.b, FakeEmbedder(), "seat@host").get(SUBJECT)
        self.assertIsNotNone(st2, "the state doc does not survive a fresh field")
        self.assertEqual(st2.tier, "live")
        self.assertIsNotNone(st2.seed, "graduated seedless")

    def test_a_pre_fix_note_is_retrievable(self):
        f = self._field()
        self._prefix_row(f, NOTE, 1000.0)
        f.backfill_seedless()
        q = FakeEmbedder().embed([NOTE])[0]   # the ask shaped like the note
        hits = [eid for eid, s in f.recall(q)]
        self.assertIn(SUBJECT, hits, f"recall misses the recovered note: {hits}")

    def test_the_sweep_self_empties(self):
        f = self._field()
        self._prefix_row(f, NOTE, 1000.0)
        f.backfill_seedless()
        self.assertEqual(f.backfill_seedless(), 0,
                         "a second pass recovered what the first already did")

    def test_a_row_that_still_yields_nothing_stays_empty(self):
        f = self._field()
        self._prefix_row(f, "   ", 1000.0)
        self.assertEqual(f.backfill_seedless(), 0)
        d = list(f.b.scan("events"))[0][1]
        self.assertEqual(d["status"], "empty",
                         "nothing to recover: the row must stay as it stands")


if __name__ == "__main__":
    unittest.main()