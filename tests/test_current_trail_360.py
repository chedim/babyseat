"""THE 3D FIELD DRAWS THE CURRENT AS A FADING GOLD TRAIL (#360, red first).

Pinned, seat side: the field's current (efield.current(), RAM only) is placed
into every cached 3D projection by the pulse's beat (fieldpulse.current_at ->
proj.place) and rides the existing /event stream as a `field.current` frame
{pid, p, tau_s} -- only when its placed position moved, and once with p=None
when it fades out; nothing at all before anyone spoke. tau_s is
efield.CURRENT_TAU_S (the viewer never hard-codes it). The existing
/membrane/map/field3d answer carries the same {p, tau_s} as `current`, so a
freshly opened view has the head at once.

Pinned, client side (pure functions, via node): alpha = exp(-age / tau);
the trail keeps at most CURRENT_TRAIL_MAX points, skips a repeat, drops what
has faded under the floor, and fills one line geometry (world positions +
RGBA colours, oldest first) in place.
"""

import json
import math
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ef_fakes as _fakes  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import field3d as F3  # noqa: E402
from membrane import fieldpulse as FP  # noqa: E402
from membrane import lanes as L  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


class _Hub:
    def __init__(self):
        self.frames = []

    def publish(self, f):
        self.frames.append(f)


class _Srv:
    pass


class _Fld:
    """Just the current: what the beat reads."""
    def __init__(self, vec=None):
        self.vec = vec

    def current(self, now=None):
        return None if self.vec is None else np.asarray(self.vec, dtype=float)


def _proj(n=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = [{"eid": f"f:e{i}", "kind": "f", "surface": f"e{i}", "accr": i, "state": rng.normal(size=8).tolist()}
            for i in range(n)]
    return F3.build(lambda: iter(rows), lambda s: s, ""), rows


def _cur(frames):
    return [f for f in frames if f["type"] == "field.current"]


class TestTheBeatCarriesTheCurrent(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv()
        self.hub = _Hub()
        self.p = FP.FieldPulse(self.srv, hub=self.hub)
        self.proj, self.rows = _proj()
        self.srv._field3d = {self.proj.pid: self.proj}

    def test_the_current_is_placed_into_each_cached_projection(self):
        self.srv.efield = _Fld(self.rows[5]["state"])
        self.p.beat()
        fr = _cur(self.hub.frames)
        self.assertEqual(len(fr), 1)
        self.assertEqual(fr[0]["pid"], self.proj.pid)
        self.assertEqual(fr[0]["p"], self.proj.place(self.rows[5]["state"], FP.CURRENT_ID))
        self.assertEqual(fr[0]["tau_s"], EF.CURRENT_TAU_S)
        self.assertLessEqual(float(np.linalg.norm(np.asarray(fr[0]["p"]) - 0.5)), 0.5 + 1e-4)

    def test_a_still_current_sends_nothing_new(self):
        self.srv.efield = _Fld(self.rows[5]["state"])
        self.p.beat()
        self.p.beat()
        self.assertEqual(len(_cur(self.hub.frames)), 1)
        self.srv.efield.vec = self.rows[9]["state"]             # it moved
        self.p.beat()
        self.assertEqual(len(_cur(self.hub.frames)), 2)

    def test_a_faded_current_says_so_once(self):
        self.srv.efield = _Fld(self.rows[5]["state"])
        self.p.beat()
        self.srv.efield.vec = None
        self.p.beat()
        self.p.beat()
        fr = _cur(self.hub.frames)
        self.assertEqual(len(fr), 2)
        self.assertIsNone(fr[1]["p"])
        self.assertEqual(fr[1]["pid"], self.proj.pid)

    def test_no_current_no_frame(self):
        self.srv.efield = _Fld(None)
        self.p.beat()
        self.assertEqual(_cur(self.hub.frames), [])
        del self.srv.efield                                     # no field at all
        self.assertEqual(self.p.beat(), 0)

    def test_no_projection_no_frame(self):
        self.srv._field3d = {}
        self.srv.efield = _Fld(self.rows[5]["state"])
        self.p.beat()
        self.assertEqual(_cur(self.hub.frames), [])

    def test_only_numbers_ride_it(self):
        self.srv.efield = _Fld(self.rows[5]["state"])
        self.p.beat()
        f = _cur(self.hub.frames)[0]
        self.assertEqual(set(f), {"type", "pid", "p", "tau_s"})
        self.assertTrue(all(isinstance(v, float) for v in f["p"]))


def _field(pulled=True):
    f = EntityField(MemBackend(), _fakes.FakeEmbedder(), "seat@host")
    for i in range(6):
        f.write_events(ST.split_turn(
            f"membrane/router.py and membrane/lanes.py talk about the gateway {i}",
            f"membrane/router.py retries; membrane/efield.py flushes round {i}"), session="s")
    f.process()
    f.flush()
    if not pulled:
        f._now.clear(); f._now_w.clear(); f._now_ts.clear()
        f._thing_ctx.clear(); f._thing_w.clear(); f._thing_ts.clear()   # #333 part 3g: the things current too
    return f


class TestTheMapRouteCarriesTheHead(unittest.TestCase):
    def test_the_answer_carries_the_current(self):
        srv = _Srv()
        srv.efield = _field()
        self.assertIsNotNone(srv.efield.current())
        out = L.field3d_answer(srv, "")
        self.assertIn("root", out)
        proj = srv._field3d[out["pid"]]
        self.assertEqual(out["current"]["tau_s"], EF.CURRENT_TAU_S)
        self.assertEqual(out["current"]["p"], proj.place(srv.efield.current(), FP.CURRENT_ID))

    def test_no_turns_no_head(self):
        srv = _Srv()
        srv.efield = _field(pulled=False)
        out = L.field3d_answer(srv, "")
        self.assertIsNone(out["current"]["p"])
        self.assertEqual(out["current"]["tau_s"], EF.CURRENT_TAU_S)


def _js(expr):
    js = ('const F=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "field3d.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestTheTrail(unittest.TestCase):
    def test_alpha_fades_with_the_seats_tau(self):
        a0, a1, a2, neg, bad = _js("[F.currentAlpha(0, 180), F.currentAlpha(180, 180), F.currentAlpha(360, 180),"
                                   " F.currentAlpha(-5, 180), F.currentAlpha(10, 0)]")
        self.assertAlmostEqual(a0, 1.0)
        self.assertAlmostEqual(a1, math.exp(-1))
        self.assertAlmostEqual(a2, math.exp(-2))
        self.assertAlmostEqual(neg, 1.0)                        # a clock skew never brightens past the head
        self.assertEqual(bad, 0)                                # no tau from the seat: nothing drawn

    def test_the_trail_is_capped_and_skips_repeats(self):
        out = _js("(() => { const t = []; F.trailPush(t, [0.1, 0.2, 0.3], 0); F.trailPush(t, [0.1, 0.2, 0.3], 5);"
                  " for (let i = 0; i < F.CURRENT_TRAIL_MAX + 50; i++) F.trailPush(t, [i / 1e4, 0.5, 0.5], 10 + i);"
                  " return [t.length, F.CURRENT_TRAIL_MAX, t[t.length - 1].t, t[0].t]; })()")
        n, cap, last_t, first_t = out
        self.assertEqual(n, cap)
        self.assertGreater(cap, 0)
        self.assertLessEqual(cap, 1000)
        self.assertEqual(last_t, 10 + cap + 49)                 # the newest kept
        self.assertEqual(first_t, 10 + 50 + 49 - 49)            # the oldest dropped first

    def test_the_trail_fills_one_geometry_oldest_first(self):
        out = _js("(() => { const t = []; F.trailPush(t, [0.5, 0.5, 0.5], 0); F.trailPush(t, [1, 0.5, 0.5], 180000);"
                  " const P = new Float32Array(30), C = new Float32Array(40);"
                  " const n = F.trailFill(t, 180000, 180, P, C, [1, 0.8, 0.3]);"
                  " return [n, Array.from(P.slice(0, 6)), Array.from(C.slice(0, 8))]; })()")
        n, P, C = out
        self.assertEqual(n, 2)
        w = _js("F.F3_WORLD")
        self.assertEqual(P[:3], [0, 0, 0])                      # the oldest: the centre, in world units
        self.assertAlmostEqual(P[3], 0.5 * w, places=3)         # the head: the outer edge
        self.assertAlmostEqual(C[3], math.exp(-1), places=5)    # 180 s old at tau 180: e^-1
        self.assertAlmostEqual(C[7], 1.0, places=5)             # the head: full
        self.assertAlmostEqual(C[0], 1.0, places=5)
        self.assertAlmostEqual(C[1], 0.8, places=5)

    def test_faded_points_are_dropped(self):
        n = _js("(() => { const t = []; F.trailPush(t, [0.2, 0.5, 0.5], 0); F.trailPush(t, [0.3, 0.5, 0.5], 1e7);"
                " const P = new Float32Array(30), C = new Float32Array(40);"
                " return [F.trailFill(t, 1e7, 180, P, C, [1, 1, 1]), t.length]; })()")
        self.assertEqual(n, [1, 1])                             # e^-55: gone from the array too

    def test_the_view_wires_it(self):
        src = (UI / "field3d.js").read_text()
        self.assertIn('"field.current"', src)
        self.assertIn("tau_s", src)
        body = src[src.index("  _stepCurrent(now) {"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("reducedMotion()", body)                  # the glow holds still under reduced motion
        self.assertIn("document.hidden", body)                  # no work when the tab is hidden
        self.assertNotIn("180", body)                           # tau comes from the seat


if __name__ == "__main__":
    unittest.main()
