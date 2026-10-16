"""THE FRESHNESS HOLES (2026-10-08, the seat's own review, red first):

  "The states-poll and Agatha ring tracks show `last ?` while others show
   real stamps. For a ship-watch, 'unknown when' is the worst state a gauge
   can be in."

An event written without a ts was stored ts=None, and _update keeps the
old last_ts when ts is None -- so entities fed only by untimed events
never got one. Their seq is the per-origin MICROSECOND clock, so the time
is always recoverable: event_ts(ts, seq) falls back to seq/1e6 (the same
value on every replay), and the banner reads last_pos's seq for states
that already lost it.
"""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402


class TestEventTs(unittest.TestCase):
    def test_seq_is_the_fallback_clock(self):
        self.assertEqual(EF.event_ts(123.5, 1791476498745484), 123.5)
        self.assertAlmostEqual(EF.event_ts(None, 1791476498745484), 1791476498.745484, places=5)
        self.assertIsNone(EF.event_ts(None, None))
        self.assertIsNone(EF.event_ts(None, 0))
        # a BACKFILL's synthetic seq (audit line * 16) is not a clock: the
        # old history stays untimed instead of reading '56y ago'
        self.assertIsNone(EF.event_ts(None, 1234 * 16 + 3))

    def test_state_last_seen_falls_back_to_last_pos(self):
        st = EF.EntityState(eid="c:agdroid:hass-states", kind="c", surface="x", tier="live")
        st.last_ts = None
        st.last_pos = (int((time.time() - 7200) * 1e6), 0)
        self.assertAlmostEqual(EF.last_seen(st), time.time() - 7200, delta=5)
        st.last_ts = 42.0
        self.assertEqual(EF.last_seen(st), 42.0)


class _Field:
    def __init__(self, st):
        self.st = st

    def get(self, eid):
        return self.st if eid == self.st.eid else None

    def resolve(self, s):
        return self.st.eid

    def graph_fn(self):
        return None

    def evidence(self, eid, k=1, **kw):
        return []


class TestBannerFreshness(unittest.TestCase):
    def test_no_more_last_question_mark(self):
        now = time.time()
        st = EF.EntityState(eid="c:agdroid:hass-states", kind="c", surface="x", tier="live")
        st.n = 3
        st.n_by_stream = {"observation": 3}
        st.last_ts = None
        # a minute past the 3h boundary: the microsecond seq's float round
        # trip can land a hair under 3h and print '2h ago' (a CI flake)
        st.last_pos = (int((now - 3 * 3600 - 60) * 1e6), 0)
        line = EB.track_line(_Field(st), st.eid, now=now)
        self.assertNotIn("last ?", line)
        self.assertIn("last 3h ago", line)


if __name__ == "__main__":
    unittest.main()
