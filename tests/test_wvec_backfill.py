"""#690: older events get their whole vector stored in the field ONCE, by a
background pass after the seat has booted -- in batches, atomically per event
(cas_update), never during a turn -- so recall reads no sentence rows for
whole-event scores after any restart."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from test_whole_event_vec_690 import _events, _field  # noqa: E402


def _strip(f):
    for k, d in _events(f):
        f.b.upsert("events", k, {**d, "wvec": None})


class TestBackfill(unittest.TestCase):
    def test_fills_every_event_once(self):
        f = _field()
        _strip(f)
        n = f.backfill_wvec(batch=2, pause=0)
        self.assertEqual(n, len(_events(f)))
        self.assertTrue(all(d.get("wvec") for _k, d in _events(f)))
        self.assertEqual(f.backfill_wvec(batch=2, pause=0), 0)

    def test_the_value_is_the_one_embed_writes(self):
        f = _field()
        want = {k: d["wvec"] for k, d in _events(f)}
        _strip(f)
        f.backfill_wvec(batch=10, pause=0)
        self.assertEqual({k: d["wvec"] for k, d in _events(f)}, want)

    def test_never_overwrites_an_event_that_changed(self):
        f = _field()
        _strip(f)
        k, d = _events(f)[0]
        real = f.b.cas_update

        def racing(coll, key, fn):              # the event's sentences change mid-pass
            if key == k:
                cur = f.b.get("events", k)
                f.b.upsert("events", k, {**cur, "sids": ["s:other"]})
            return real(coll, key, fn)
        f.b.cas_update = racing
        f.backfill_wvec(batch=10, pause=0)
        self.assertFalse(f.b.get("events", k).get("wvec"))

    def test_stops_when_asked(self):
        f = _field()
        _strip(f)
        self.assertEqual(f.backfill_wvec(batch=1, pause=0, stop=lambda: True), 0)

    def test_the_seat_starts_it_after_boot(self):
        src = (Path(__file__).parent.parent / "membrane" / "opencode.py").read_text()
        self.assertLess(src.index("_RS.on_boot(srv)"), src.index("backfill_wvec"))


if __name__ == "__main__":
    unittest.main()
