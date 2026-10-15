"""THE DELTA BITS (2026-10-15, stage 1 of the delta lane -- store only).

The cortex-experiment finding, reproduced on membrane's own embedder: the
DIRECTION of an embedding's move names the KIND of change, across sensors
the recall never saw (12 text-rendered sensors, mxbai, leave-one-sensor-out:
delta 0.631 / sign(R @ delta) 0.628 / the before+after pair 0.460 / the
after-state 0.363, chance 0.333). The field already computed the move at
every entity update (x - before) and kept only its cosine; the old cortex
kept only its norm. Stage 1 keeps the direction: sign(R @ (x - before))
packed on the entity_deltas row, with R the VSA codebook's own projection
(its first DELTA_DIM rows -- the same seed, so every seat's bits agree).

Nothing reads the bits yet: no behavior changes; the recall comes after the
real-history rung says it earns it."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane import vsa  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def _move(seed=0, dim=1024):
    r = np.random.default_rng(seed)
    return r.standard_normal(dim)


class TestTheBits(unittest.TestCase):
    """vsa.delta_bits: the move's direction as packed sign bits."""

    def test_the_same_move_from_different_places_reads_the_same(self):
        m = _move(1)
        a, b = _move(2), _move(3)
        self.assertEqual(vsa.delta_bits(a, a + m), vsa.delta_bits(b, b + m))

    def test_the_opposite_move_reads_the_complement(self):
        a, m = _move(4), _move(5)
        up = vsa.unpack_state(vsa.delta_bits(a, a + m))
        down = vsa.unpack_state(vsa.delta_bits(a, a - m))
        self.assertGreater(vsa.hamming(up, down), 0.95)

    def test_unrelated_moves_read_unrelated(self):
        a = _move(6)
        h = vsa.hamming(vsa.unpack_state(vsa.delta_bits(a, a + _move(7))),
                        vsa.unpack_state(vsa.delta_bits(a, a + _move(8))))
        self.assertTrue(0.4 < h < 0.6, h)

    def test_no_move_has_no_direction(self):
        a = _move(9)
        self.assertIsNone(vsa.delta_bits(a, a.copy()))

    def test_the_width_is_delta_dim(self):
        a = _move(10)
        self.assertEqual(len(vsa.unpack_state(vsa.delta_bits(a, a + _move(11)))),
                         vsa.DELTA_DIM)

    def test_a_narrow_embedder_pads_like_the_codebook(self):
        a = _move(12, dim=64)
        self.assertIsNotNone(vsa.delta_bits(a, a + _move(13, dim=64)))

    def test_every_seat_agrees_the_projection_is_the_codebooks(self):
        """Cross-seat comparability: the bits are the codebook projection's
        first DELTA_DIM rows (the same seed), never a private matrix."""
        a, m = _move(14), _move(15)
        cb = vsa.VSACodebook(dim=vsa.DEFAULT_DIM)
        want = (cb._proj[:vsa.DELTA_DIM] @ m >= 0).astype(np.uint8)
        got = vsa.unpack_state(vsa.delta_bits(a, a + m))
        np.testing.assert_array_equal(got, want)


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")


def _say(f, texts):
    evs = [ST.Event(stream="discourse", role="user", text=t, ts=1000.0 + i)
           for i, t in enumerate(texts)]
    f.write_events(evs, session="s1")
    f.process()


_TEXTS = ["edit membrane/router.py to pick the cheapest model",
          "membrane/router.py now routes by cost",
          "membrane/router.py broke the tests"]


def _rows(f, eid):
    return sorted((d for _, d in f.b.scan("entity_deltas") if d.get("eid") == eid),
                  key=lambda d: (d["seq"], d["sidx"]))


class TestTheFieldKeepsTheDirection(unittest.TestCase):
    """The entity_deltas row carries the move's bits (stage 1: stored only)."""

    def test_a_specific_entitys_rows_carry_the_bits(self):
        f = _field()
        _say(f, _TEXTS)
        rows = _rows(f, "f:membrane/router.py")
        self.assertEqual(len(rows), 3)
        for d in rows:
            self.assertIn("dbits", d)
            self.assertEqual(len(vsa.unpack_state(d["dbits"])), vsa.DELTA_DIM)

    def test_the_event_index_rows_stay_bare(self):
        """The e: rows map an event, they have no state to move."""
        f = _field()
        _say(f, _TEXTS)
        idx = [d for _, d in f.b.scan("entity_deltas") if str(d.get("eid", "")).startswith("e:")]
        self.assertTrue(idx)
        self.assertFalse([d for d in idx if "dbits" in d])

    def test_a_rebuild_replays_the_same_bits(self):
        f = _field()
        _say(f, _TEXTS)
        before = [d["dbits"] for d in _rows(f, "f:membrane/router.py")]
        f.rebuild()
        self.assertEqual([d["dbits"] for d in _rows(f, "f:membrane/router.py")], before)

    def test_the_vsa_kill_switch_keeps_the_rows_bare(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_VSA": "0"}):
            f = _field()
            _say(f, _TEXTS)
        self.assertFalse([d for d in _rows(f, "f:membrane/router.py") if "dbits" in d])


if __name__ == "__main__":
    unittest.main()
