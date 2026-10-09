"""THE WHOLE FIELD IN 3D (2026-10-08, the operator's viewer v3, red first).

The 2D map clamped to the top-3000 by mass (of ~14k live). Pinned: every
live entity gets a cube position; the octree puts each in exactly one tile
(K per cell, heaviest coarsest) so zooming in shows more; three axes (PCs,
kinds, entity distance / toward); the fit streams (no N x 1024 matrix kept);
place() re-positions a moved entity under the same fit.
"""

import os
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import field3d as F  # noqa: E402

D = 16


def _rows(n, seed=0):
    rng = np.random.default_rng(seed)
    centers = {"f": rng.normal(size=D), "s": rng.normal(size=D), "w": rng.normal(size=D)}
    out = []
    for i in range(n):
        kd = "fsw"[i % 3]
        v = centers[kd] + 0.3 * rng.normal(size=D)
        out.append({"eid": f"{kd}:e{i}", "kind": kd, "surface": f"e{i}", "accr": int(rng.integers(1, 500)),
                    "conflicts": [], "recent": [f"sid{i // 4}"], "state": v.tolist()})
    return out


def _build(rows, axes="", k=20, **kw):
    return F.build(lambda: iter(rows), lambda s: s, axes, ref_vec=lambda e: next(
        (r["state"] for r in rows if r["eid"] == e), None), tile_k=k, **kw)


class TestWholeField(unittest.TestCase):
    def test_every_entity_is_placed_inside_the_cube(self):
        rows = _rows(5000)
        p = _build(rows)
        self.assertEqual(p.meta()["n_total"], 5000)            # not 3000
        self.assertTrue(((p.coords >= 0) & (p.coords <= 1)).all())

    def test_each_entity_lives_in_exactly_one_tile(self):
        rows = _rows(3000)
        p = _build(rows, k=20)
        seen = []
        for (lv, x, y, z) in list(p.tiles):
            t = p.tile(lv, x, y, z)
            self.assertLessEqual(len(t["nodes"]), 20 if lv < F.MAX_LEVEL else 10 ** 9)
            seen += [nd["id"] for nd in t["nodes"]]
        self.assertEqual(sorted(seen), sorted(r["eid"] for r in rows))

    def test_heavier_entities_sit_coarser(self):
        rows = _rows(3000)
        p = _build(rows, k=20)
        heavy = max(rows, key=lambda r: r["accr"])["eid"]
        self.assertEqual(p.locate(heavy)["lv"], 0)
        self.assertGreater(p.max_level, 1)

    def test_zooming_in_shows_more(self):
        rows = _rows(3000)
        p = _build(rows, k=20)
        per = p.meta()["per_level"]
        self.assertEqual(sum(per), 3000)
        self.assertLessEqual(per[0], 20)

    def test_more_marks_only_cells_with_finer_entities(self):
        rows = _rows(3000)
        p = _build(rows, k=20)
        self.assertTrue(p.tile(0, 0, 0, 0)["more"])
        for (lv, x, y, z) in list(p.tiles):
            t = p.tile(lv, x, y, z)
            finer = any(l2 > lv and (x2 >> (l2 - lv), y2 >> (l2 - lv), z2 >> (l2 - lv)) == (x, y, z)
                        for (l2, x2, y2, z2) in p.tiles)
            self.assertEqual(t["more"], finer, (lv, x, y, z))
        self.assertFalse(p.tile(p.max_level, 0, 0, 0)["more"])

    def test_tile_edges_reference_loaded_levels_only(self):
        rows = _rows(1200)
        p = _build(rows, k=10)
        for (lv, x, y, z) in list(p.tiles)[:40]:
            for e in p.tile(lv, x, y, z)["edges"]:
                for end in (e["s"], e["t"]):
                    self.assertLessEqual(p.locate(end)["lv"], lv)


class TestAxes(unittest.TestCase):
    def test_parse_fills_with_pcs(self):
        self.assertEqual(F.parse_axes(""), ["pc1", "pc2", "pc3"])
        self.assertEqual(F.parse_axes("e:f:e3,pc1"), ["e:f:e3", "pc1", "pc2"])

    def test_entity_distance_axis_puts_the_reference_at_zero(self):
        rows = _rows(900)
        p = _build(rows, "e:f:e3,pc1,pc2")
        ref = p.locate("f:e3")["p"][0]
        self.assertLess(ref, 0.1)                             # nearest to itself
        same_kind = np.mean([p.locate(f"f:e{i}")["p"][0] for i in range(0, 900, 3)])
        other = np.mean([p.locate(f"s:e{i}")["p"][0] for i in range(1, 900, 3)])
        self.assertLess(same_kind, other)

    def test_toward_and_kind_axes(self):
        rows = _rows(900)
        p = _build(rows, "e:f:e3:toward,kind:s,pc1")
        f = np.mean([p.locate(f"f:e{i}")["p"][0] for i in range(0, 900, 3)])
        s = np.mean([p.locate(f"s:e{i}")["p"][0] for i in range(1, 900, 3)])
        self.assertGreater(f, s)
        ks = np.mean([p.locate(f"s:e{i}")["p"][1] for i in range(1, 900, 3)])
        kf = np.mean([p.locate(f"f:e{i}")["p"][1] for i in range(0, 900, 3)])
        self.assertGreater(ks, kf)

    def test_place_matches_the_fit(self):
        rows = _rows(900)
        p = _build(rows)
        for r in rows[:20]:
            got = p.place(r["state"])
            want = p.locate(r["eid"])["p"]
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, delta=0.02)


class TestStreaming(unittest.TestCase):
    def test_the_fit_uses_a_bounded_sample(self):
        rows = _rows(4000)
        p = F.build(lambda: iter(rows), lambda s: s, "", sample_top=200, sample_rand=50, chunk=128)
        self.assertEqual(p.meta()["n_total"], 4000)

    def test_rows_are_read_twice_not_held(self):
        rows = _rows(500)
        calls = []

        def factory():
            calls.append(1)
            return iter(rows)
        F.build(factory, lambda s: s, "")
        self.assertEqual(len(calls), 2)

    def test_an_empty_field(self):
        p = F.build(lambda: iter([]), lambda s: s, "")
        self.assertEqual(p.meta()["n_total"], 0)


if __name__ == "__main__":
    unittest.main()
