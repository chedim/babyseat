"""THE 3D FIELD IS A RUSSIAN-DOLL SPHERE, NOT A CUBE (#362, red first).

Pinned: an entity's DIRECTION from the centre is its projection onto the
three axes, normalised; its DISTANCE is how much of its (centred) vector
those three axes capture, spread by rank (the cube's per-axis recipe, once,
on the radius). Every position lies inside the ball inscribed in [0,1]^3;
a vector lying in the axes' span sits on the outer shell, one orthogonal to
it near the centre; place() lands a moved entity on the same sphere; the
octree still files each entity in exactly one tile. The client draws shells,
not the cube's edges, frames the whole ball, dims the far side and labels
the heaviest near-side entities.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import field3d as F  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")
D = 16


def _rows(n, seed=0):
    rng = np.random.default_rng(seed)
    centers = {"f": rng.normal(size=D), "s": rng.normal(size=D), "w": rng.normal(size=D)}
    out = []
    for i in range(n):
        kd = "fsw"[i % 3]
        v = centers[kd] + 0.3 * rng.normal(size=D)
        out.append({"eid": f"{kd}:e{i}", "kind": kd, "surface": f"e{i}", "accr": int(rng.integers(1, 500)),
                    "conflicts": [], "recent": [], "state": v.tolist()})
    return out


def _build(rows, axes="", k=20):
    return F.build(lambda: iter(rows), lambda s: s, axes, ref_vec=lambda e: next(
        (r["state"] for r in rows if r["eid"] == e), None), tile_k=k)


def _span_rows(n=600, seed=1):
    """Entities in a D-space whose first three dims carry a controlled share:
    'in' rows live in dims 0..2 only, 'out' rows in dims 3.. only, the rest
    mixed; e0..e2 axes are those dims (kind axes would be fuzzier)."""
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        v = rng.normal(size=D)
        if i == 0:
            v[3:] = 0.0                                    # fully inside the span
        elif i == 1:
            v[:3] = 0.0                                    # orthogonal to it
        rows.append({"eid": f"x:e{i}", "kind": "x", "surface": f"e{i}", "accr": 1 + i % 7,
                     "conflicts": [], "recent": [], "state": v.tolist()})
    # centre the cloud so the mean is ~0 and the fit's axes are the dims
    mu = np.mean([r["state"] for r in rows], axis=0)
    for r in rows:
        r["state"] = (np.asarray(r["state"]) - mu).tolist()
    for j in range(3):                                     # reference entities = the axes
        e = np.zeros(D)
        e[j] = 50.0
        rows.append({"eid": f"ref:{j}", "kind": "ref", "surface": f"ref{j}", "accr": 1,
                     "conflicts": [], "recent": [], "state": e.tolist()})
    return rows


def _axes_of(rows):
    """The fit's mean and the three toward-axes (mean -> ref:j), as the build sees them."""
    mu = np.mean([r["state"] for r in rows], axis=0)
    A = []
    for j in range(3):
        d = np.asarray(next(r["state"] for r in rows if r["eid"] == f"ref:{j}")) - mu
        A.append(d / np.linalg.norm(d))
    return mu, np.asarray(A)


def _r(p):
    return float(np.linalg.norm(np.asarray(p, dtype=float) - 0.5))


class TestTheBall(unittest.TestCase):
    def test_every_position_lies_inside_the_ball(self):
        p = _build(_rows(3000))
        r = np.linalg.norm(p.coords.astype(float) - 0.5, axis=1)
        self.assertTrue((r <= 0.5 + 1e-4).all())
        self.assertTrue((r > 0).all())                     # radius in (0, 1]: nobody AT the centre
        self.assertEqual(p.meta().get("shape"), "sphere")

    def test_the_ball_is_filled_evenly_by_volume(self):
        # radius = cube root of the spread rank: a uniform rank fills the ball's VOLUME
        # evenly (a uniform radius would crowd the core, density ~ 1/r^2)
        p = _build(_rows(3000))
        r = np.linalg.norm(p.coords.astype(float) - 0.5, axis=1) / 0.5
        inner = float((r <= 0.5).mean())                    # the inner half-radius holds 1/8 of the volume
        self.assertGreater(inner, 0.08)
        self.assertLess(inner, 0.2)

    def test_in_span_sits_on_the_outer_shell_orthogonal_near_the_centre(self):
        rows = _span_rows()
        p = _build(rows, "e:ref:0:toward,e:ref:1:toward,e:ref:2:toward")
        rin, rout = _r(p.locate("x:e0")["p"]), _r(p.locate("x:e1")["p"])
        self.assertGreater(rin, 0.49)                       # the outer shell
        others = [_r(p.locate(r["eid"])["p"]) for r in rows if r["eid"].startswith("x:") and r["eid"] != "x:e1"]
        self.assertLess(rout, min(others))                  # the innermost of them all
        # the order of the captured fraction survives the rank spread
        X = np.asarray([r["state"] for r in rows if r["eid"].startswith("x:")])
        mu, A = _axes_of(rows)
        C = X - mu
        frac = np.linalg.norm(C @ A.T, axis=1) / np.linalg.norm(C, axis=1)
        radii = np.asarray([_r(p.locate(f"x:e{i}")["p"]) for i in range(len(X))])
        order = np.argsort(frac)
        self.assertTrue((np.diff(radii[order]) >= -1e-3).all())

    def test_direction_follows_the_projection_sign(self):
        rows = _span_rows()
        p = _build(rows, "e:ref:0:toward,e:ref:1:toward,e:ref:2:toward")
        mu, A = _axes_of(rows)
        for i in range(2, 60):
            pr = A @ (np.asarray(rows[i]["state"]) - mu)
            pos = np.asarray(p.locate(f"x:e{i}")["p"]) - 0.5
            for k in range(3):
                if abs(pr[k]) > 0.05:
                    self.assertEqual(np.sign(pos[k]), np.sign(pr[k]), (i, k))
            # the direction is the projection, normalised
            d = pr / np.linalg.norm(pr)
            self.assertGreater(float(pos @ d) / np.linalg.norm(pos), 0.999)

    def test_plain_entity_axis_is_linear_toward_the_entity(self):
        rows = _span_rows()
        a = _build(rows, "e:ref:0,e:ref:1,e:ref:2")
        b = _build(rows, "e:ref:0:toward,e:ref:1:toward,e:ref:2:toward")
        for i in range(0, 40):
            np.testing.assert_allclose(a.locate(f"x:e{i}")["p"], b.locate(f"x:e{i}")["p"], atol=1e-3)
        self.assertGreater(a.locate("ref:0")["p"][0], 0.5)  # the reference: on its own axis' + side

    def test_place_reproduces_a_built_position(self):
        rows = _rows(900)
        p = _build(rows)
        for r in rows[:40]:
            got = p.place(r["state"], r["eid"])
            want = p.locate(r["eid"])["p"]
            np.testing.assert_allclose(got, want, atol=0.01)
            self.assertLessEqual(_r(p.place(r["state"])), 0.5 + 1e-4)

    def test_place_of_a_far_out_vector_stays_inside(self):
        p = _build(_rows(600))
        got = p.place((np.ones(D) * 1e6).tolist())
        self.assertLessEqual(_r(got), 0.5 + 1e-4)
        self.assertEqual(len(got), 3)

    def test_an_entity_at_the_projection_centre_gets_a_stable_direction(self):
        rows = _span_rows()
        p = _build(rows, "e:ref:0:toward,e:ref:1:toward,e:ref:2:toward")
        mu = np.mean([r["state"] for r in rows], axis=0)
        v = mu.copy()
        v[5] += 3.0                                         # nothing on the three axes
        a, b = p.place(v.tolist(), "w:zero"), p.place(v.tolist(), "w:zero")
        self.assertEqual(a, b)
        self.assertTrue(all(np.isfinite(a)))
        built = [_r(p.locate(r["eid"])["p"]) for r in rows if r["eid"].startswith("x:")]
        self.assertLessEqual(_r(a), min(built) + 1e-4)     # as deep as the innermost built entity

    def test_each_entity_still_lives_in_exactly_one_tile(self):
        rows = _rows(3000)
        p = _build(rows, k=20)
        seen = []
        for (lv, x, y, z) in list(p.tiles):
            seen += [nd["id"] for nd in p.tile(lv, x, y, z)["nodes"]]
        self.assertEqual(sorted(seen), sorted(r["eid"] for r in rows))


def _js(expr):
    js = ('const F=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "field3d.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestTheClient(unittest.TestCase):
    def test_the_cube_edges_are_gone(self):
        src = (UI / "field3d.js").read_text()
        self.assertNotIn("BoxGeometry", src)
        self.assertIn("shellCircles", src)

    def test_shell_circles_lie_on_their_shells(self):
        out = _js("F.shellCircles(500, [1, 0.5], 24)")
        self.assertTrue(out)
        for c in out:
            for q in c["pts"]:
                self.assertAlmostEqual((q[0] ** 2 + q[1] ** 2 + q[2] ** 2) ** 0.5, 500 * c["r"], delta=1e-6)

    def test_the_default_camera_frames_the_whole_ball(self):
        import math
        for aspect in (0.5, 1.0, 2.0):
            d = _js("F.frameBall(500, 55 * Math.PI / 180, %r)" % aspect)
            fy = math.radians(55) / 2
            fx = math.atan(math.tan(fy) * aspect)
            self.assertGreaterEqual(500 / d, 0)
            self.assertLessEqual(500 / d, math.sin(min(fx, fy)) + 1e-9)    # the ball fits both ways
            self.assertGreater(500 / d, 0.7 * math.sin(min(fx, fy)))       # and fills the view

    def test_farther_is_dimmer(self):
        near, mid, far = _js("[F.depthFade(1000, 1500, 500), F.depthFade(1500, 1500, 500), F.depthFade(2000, 1500, 500)]")
        self.assertEqual(near, 1)
        self.assertGreater(near, mid)
        self.assertGreater(mid, far)
        self.assertGreater(far, 0)

    def test_labels_pick_the_heaviest_on_the_near_side(self):
        out = _js("[F.nearSide([0, 0, 400], [0, 0, 1500], [0, 0, 0]), F.nearSide([0, 0, -400], [0, 0, 1500], [0, 0, 0])]")
        self.assertEqual(out, [True, False])


if __name__ == "__main__":
    unittest.main()
