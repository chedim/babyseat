"""#257 (part of #161): self's amoeba acts out what self is doing.

The body itself acts out self's record, driven only by real record lines and
the cycle frame, nothing scripted: a move stretches it along the step, a look
grows a pseudopod toward the thing, a surprise jolts it (sized by the error),
a shift startles it, a wait breathes, a push ripples toward "you" and then it
listens, a reply check bounces (or droops), a toy self changed gets a wiggle,
a level-up glows. Nothing happening: the calm baseline.

The pose logic is pure (field3d.js, F3Logic) and is run here with node.
"""
import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _f3(expr):
    js = ('const F=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "field3d.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


def _kinds(r):
    return [a["kind"] for a in _f3("F.amoebaActs(%s)" % json.dumps(r))]


def _pose(act, now, rm=False):
    return _f3("F.poseFor(%s, %s, %s)" % (json.dumps(act), json.dumps(now), "true" if rm else "false"))


def _calm():
    return _f3("F.calmPose()")


def _off(p):
    return math.hypot(p["ox"], p["oy"], p["oz"])


def _st(p):
    return math.hypot(p["sx"], p["sy"], p["sz"])


def _diff(p, q):
    return max(abs(p[k] - q[k]) for k in p)


X = [1, 0, 0]


# ---- the record line -> what the body acts out ------------------------------------

@NODE
class TestEachKindMapsToItsAct(unittest.TestCase):
    def test_a_move(self):
        acts = _f3("F.amoebaActs({kind: 'cycle', act: 'move', step: 0.2, aim_n: 12})")
        self.assertEqual([a["kind"] for a in acts], ["move"])
        self.assertEqual(acts[0]["stride"], 0.2)
        self.assertTrue(acts[0]["steer"])
        self.assertFalse(_f3("F.amoebaActs({kind: 'cycle', act: 'move', step: 0.2})")[0]["steer"])

    def test_a_look_and_a_surprise(self):
        self.assertEqual(_kinds({"kind": "reach", "eid": "w:x", "answer": "changed", "p": 0.9}), ["look"])
        self.assertEqual(_kinds({"kind": "reach", "eid": "w:x", "answer": "changed", "p": 0.1}), ["look", "surprise"])
        self.assertEqual(_kinds({"kind": "reach", "eid": "w:x", "answer": "unchanged", "p": 0.95}), ["look", "surprise"])
        # no answer, or no expectation: nothing to be surprised by
        self.assertEqual(_kinds({"kind": "reach", "eid": "w:x", "answer": "none", "p": 0.95}), ["look"])
        self.assertEqual(_kinds({"kind": "reach", "eid": "w:x", "answer": "changed"}), ["look"])

    def test_the_error_is_outcome_minus_p(self):
        s = _f3("F.amoebaActs({kind: 'reach', answer: 'unchanged', p: 0.8})")[1]
        self.assertAlmostEqual(s["err"], 0.8, places=6)

    def test_the_jolt_and_the_wiggle_come_when_the_answer_lands(self):
        acts = _f3("F.amoebaActs({kind: 'reach', eid: 'toy:die', answer: 'unchanged', p: 0.95, by: ['self']})")
        lands = _f3("F.LOOK_LANDS")
        self.assertEqual({a["kind"]: a.get("delay", 0) for a in acts}, {"look": 0, "surprise": lands, "toy": lands})
        # the moment the pseudopod starts back
        look = {"kind": "look", "t0": 0, "dir": X, "reach": 1}
        self.assertEqual(_pose(look, lands - 1)["reach"], 1)
        self.assertLess(_pose(look, lands + 120)["reach"], 1)
        # a delayed act is not alive (no pose) before its time
        self.assertFalse(_f3("F.actAlive({kind: 'surprise', t0: 500, err: 0.9}, 200)"))

    def test_a_toy_self_changed_gets_a_wiggle(self):
        self.assertEqual(_kinds({"kind": "reach", "eid": "membrane::Component:toy:die", "answer": "changed", "p": 0.9,
                                 "by": ["self"]}), ["look", "toy"])
        self.assertEqual(_kinds({"kind": "reach", "eid": "membrane::Component:toy:die", "answer": "changed", "p": 0.9,
                                 "by": ["world"]}), ["look"])

    def test_the_reply_check(self):
        self.assertEqual(_kinds({"kind": "reach", "reply": True, "answer": "changed", "p": 0.4}), ["bounce"])
        self.assertEqual(_kinds({"kind": "reach", "reply": True, "answer": "unchanged", "p": 0.4}), ["droop"])
        self.assertEqual(_kinds({"kind": "reach", "reply": True, "answer": "none"}), [])

    def test_the_rest(self):
        self.assertEqual(_kinds({"kind": "shift", "eid": "w:p"}), ["startle"])
        self.assertEqual(_kinds({"kind": "wait", "n": 3}), ["wait"])
        self.assertEqual(_kinds({"kind": "push", "to": "chat:in:x"}), ["push", "listen"])
        self.assertEqual(_kinds({"kind": "rung", "rung": 7}), ["glow"])

    def test_unknown_or_absent_activity_acts_out_nothing(self):
        for r in ({"kind": "audit"}, {"kind": "birth"}, {"kind": "zzz"}, {}, None, {"kind": "cycle", "act": "rest"}):
            self.assertEqual(_f3("F.amoebaActs(%s)" % json.dumps(r)), [], r)


# ---- the poses --------------------------------------------------------------------

@NODE
class TestPoses(unittest.TestCase):
    def test_the_calm_baseline(self):
        c = _calm()
        for k, v in c.items():
            self.assertEqual(v, 0, k)

    def test_unknown_or_absent_act_is_calm(self):
        self.assertEqual(_pose({"kind": "zzz", "t0": 0}, 300), _calm())
        self.assertEqual(_f3("F.posesAt([], 500, false)"), _calm())
        self.assertEqual(_f3("F.posesAt(null, 500, false)"), _calm())

    def test_a_move_stretches_along_the_step_scaled_by_the_stride(self):
        short = _pose({"kind": "move", "t0": 0, "dir": X, "stride": 0.05}, 600)
        long = _pose({"kind": "move", "t0": 0, "dir": X, "stride": 0.3}, 600)
        self.assertGreater(short["sx"], 0)
        self.assertGreater(long["sx"], short["sx"])
        self.assertAlmostEqual(long["sy"], 0)
        self.assertAlmostEqual(_off(long), 0)                   # no aim: no lean

    def test_a_steering_move_leans_toward_its_aim(self):
        p = _pose({"kind": "move", "t0": 0, "dir": X, "stride": 0.2, "aimDir": [0, 0, 1]}, 600)
        self.assertGreater(p["oz"], 0)

    def test_a_look_grows_a_pseudopod_then_snaps_back(self):
        out = [_pose({"kind": "look", "t0": 0, "dir": [0, 1, 0], "reach": 1}, t) for t in (300, 900, 2600)]
        self.assertGreater(out[1]["reach"], out[0]["reach"])
        self.assertGreater(out[1]["reach"], 0.8)
        self.assertGreater(out[1]["py"], 0.9)                   # toward the thing
        self.assertEqual(out[2]["reach"], 0)                    # back once the answer landed

    def test_a_near_thing_gets_a_short_pseudopod(self):
        far = _pose({"kind": "look", "t0": 0, "dir": X, "reach": 1}, 900)
        near = _pose({"kind": "look", "t0": 0, "dir": X, "reach": 0.3}, 900)
        self.assertLess(near["reach"], far["reach"])

    def test_surprise_scales_with_the_error(self):
        def peak(err):
            return max(_off(_pose({"kind": "surprise", "t0": 0, "dir": X, "err": err}, t)) for t in range(0, 700, 20))
        self.assertGreater(peak(0.95), peak(0.7))
        self.assertGreater(peak(0.7), 0)
        self.assertEqual(peak(0.4), 0)                          # it half-expected it: no jolt

    def test_a_shift_puffs_up_then_settles(self):
        early = _pose({"kind": "startle", "t0": 0}, 150)
        late = _pose({"kind": "startle", "t0": 0}, 1500)
        self.assertGreater(early["puff"], 0.2)
        self.assertLess(abs(late["puff"]), early["puff"])

    def test_a_wait_breathes_slowly_and_curls(self):
        ps = [_pose({"kind": "wait", "t0": 0}, t) for t in range(1000, 6000, 250)]
        puffs = [p["puff"] for p in ps]
        self.assertGreater(max(puffs) - min(puffs), 0.04)       # it breathes
        self.assertTrue(all(p["squash"] > 0 for p in ps))        # curled
        self.assertLess(sum(puffs) / len(puffs), 0)              # a little smaller

    def test_a_push_ripples_toward_you_then_listens(self):
        ripple = [_pose({"kind": "push", "t0": 0, "dir": X}, t) for t in range(0, 1400, 20)]
        self.assertGreater(max(p["ox"] for p in ripple), 0.1)
        listen = _pose({"kind": "listen", "t0": 0, "dir": X}, 60000)
        self.assertEqual(listen["still"], 1)
        self.assertGreater(listen["ox"], 0)                       # leaning toward you

    def test_the_reply_check_bounces_or_droops(self):
        b = max((_pose({"kind": "bounce", "t0": 0, "dir": X}, t) for t in range(0, 1500, 20)), key=lambda p: p["ox"])
        self.assertGreater(b["ox"], 0.15)
        d = _pose({"kind": "droop", "t0": 0, "dir": X}, 1500)
        self.assertLess(d["oy"], 0)
        self.assertGreater(d["squash"], 0)
        self.assertGreater(d["dim"], 0)

    def test_a_toy_wiggles_toward_the_toy(self):
        ps = [_pose({"kind": "toy", "t0": 0, "dir": X}, t) for t in range(0, 1500, 20)]
        xs = [p["ox"] for p in ps]
        self.assertGreater(max(xs), 0.05)
        self.assertGreater(max(p["sx"] for p in ps), 0.05)       # leaning out toward it
        self.assertGreater(sum(1 for a, b in zip(xs, xs[1:]) if (a - 0.0) * (b - 0.0) < 0), 2)   # it wiggles

    def test_a_level_up_glows(self):
        self.assertGreater(_pose({"kind": "glow", "t0": 0}, 1500)["glow"], 0.8)


# ---- time: everything goes back to calm -------------------------------------------

ACTS = [{"kind": "move", "dir": X, "stride": 0.2, "aimDir": [0, 0, 1]}, {"kind": "look", "dir": X, "reach": 1},
        {"kind": "surprise", "dir": X, "err": 0.9}, {"kind": "startle"}, {"kind": "wait"},
        {"kind": "push", "dir": X}, {"kind": "bounce", "dir": X}, {"kind": "droop", "dir": X},
        {"kind": "toy", "dir": X}, {"kind": "glow"}]


@NODE
class TestDecay(unittest.TestCase):
    def test_every_pose_decays_back_to_baseline(self):
        for act in ACTS:
            a = dict(act, t0=1000)
            life = _f3("F.actLife(%s)" % json.dumps(a))
            self.assertGreater(life, 0, act)
            self.assertLessEqual(life, 12000, act)
            self.assertGreater(_diff(_pose(a, 1000 + life * 0.4), _calm()), 0.01, act)
            self.assertEqual(_pose(a, 1000 + life + 1), _calm(), act)
            self.assertFalse(_f3("F.actAlive(%s, %d)" % (json.dumps(a), 1000 + life + 1)), act)
            # it eases out: the last stretch is small
            self.assertLess(_diff(_pose(a, 1000 + life * 0.98), _calm()), 0.12, act)

    def test_listening_lasts_until_the_reply_check(self):
        acts = _f3("(() => { const a = []; F.addAct(a, {kind: 'listen', t0: 0, dir: [1,0,0]});"
                   " F.addAct(a, {kind: 'move', t0: 10, dir: [1,0,0], stride: 0.2});"
                   " F.addAct(a, {kind: 'bounce', t0: 20, dir: [1,0,0]}); return a.map((x) => x.kind); })()")
        self.assertEqual(acts, ["move", "bounce"])
        self.assertTrue(_f3("F.actAlive({kind: 'listen', t0: 0}, 120000)"))

    def test_the_act_list_is_capped(self):
        n = _f3("(() => { const a = []; for (let i = 0; i < 50; i++) F.addAct(a, {kind: 'startle', t0: i}); return a.length; })()")
        self.assertLessEqual(n, 12)

    def test_prune_drops_the_finished_in_place(self):
        out = _f3("(() => { const a = [{kind: 'startle', t0: 0}, {kind: 'glow', t0: 0}]; const b = a;"
                  " F.pruneActs(a, 3000); return [a === b, a.map((x) => x.kind)]; })()")
        self.assertEqual(out, [True, ["glow"]])
        waiting = _f3("F.pruneActs([{kind: 'surprise', t0: 5000, err: 0.9}], 3000).length")
        self.assertEqual(waiting, 1)                              # a delayed act waits for its time

    def test_the_blend_sums_and_clamps(self):
        p = _f3("F.posesAt([%s], 300, false)" % ",".join(
            json.dumps({"kind": "startle", "t0": 0}) for _ in range(8)))
        self.assertLessEqual(p["puff"], 0.8)
        both = _f3("F.posesAt([{kind: 'glow', t0: 0}, {kind: 'move', t0: 0, dir: [1,0,0], stride: 0.2}], 600, false)")
        self.assertGreater(both["glow"], 0)
        self.assertGreater(both["sx"], 0)

    def test_the_blend_reuses_its_output(self):
        same = _f3("(() => { const o = F.calmPose(); return F.posesAt([{kind: 'glow', t0: 0}], 500, false, o) === o; })()")
        self.assertTrue(same)


# ---- reduced motion: a held shape per state, no motion ----------------------------

@NODE
class TestReducedMotion(unittest.TestCase):
    def test_a_static_pose_while_the_act_lasts(self):
        for act in ACTS:
            a = dict(act, t0=0)
            life = _f3("F.actLife(%s)" % json.dumps(a))
            ps = [_pose(a, t, rm=True) for t in (1, life * 0.2, life * 0.5, life * 0.9)]
            for p in ps[1:]:
                self.assertEqual(p, ps[0], act)                   # held, not moving
            self.assertGreater(_diff(ps[0], _calm()), 0.01, act)  # but still a shape
            self.assertEqual(_pose(a, life + 1, rm=True), _calm(), act)

    def test_the_held_surprise_still_scales_with_the_error(self):
        lo = _off(_pose({"kind": "surprise", "t0": 0, "dir": X, "err": 0.7}, 100, rm=True))
        hi = _off(_pose({"kind": "surprise", "t0": 0, "dir": X, "err": 0.95}, 100, rm=True))
        self.assertGreater(hi, lo)


# ---- the body on the screen: an ellipse from the stretch and the squash -----------

@NODE
class TestShape(unittest.TestCase):
    def test_calm_is_a_circle(self):
        s = _f3("F.bodyShape(0, 0, 0, 0)")
        self.assertAlmostEqual(s["a"], 1, places=6)
        self.assertAlmostEqual(s["b"], 1, places=6)

    def test_a_stretch_along_a_screen_direction(self):
        s = _f3("F.bodyShape(0, 1, 0.5, 0)")          # straight up the screen
        self.assertGreater(s["a"], 1)
        self.assertLess(s["b"], 1)
        self.assertAlmostEqual(abs(math.sin(s["rot"])), 1, places=4)   # the long axis points up

    def test_a_squash_is_wide_and_flat(self):
        s = _f3("F.bodyShape(0, 0, 0, 0.2)")
        self.assertGreater(s["a"], s["b"])
        self.assertAlmostEqual(math.cos(s["rot"]) ** 2, 1, places=4)

    def test_reuses_its_output(self):
        self.assertTrue(_f3("(() => { const o = {}; return F.bodyShape(1, 0, 0.3, 0.1, o) === o; })()"))


@NODE
class TestFaceOn(unittest.TestCase):
    """◎ self chases from behind, so a step (or a look) points INTO the screen
    and a stretch along it would vanish end-on. A motion's direction (camera
    space, unit) keeps at least `min` of itself in the screen plane, turned
    the way it leans on screen (up the screen when it heads straight away)."""

    def test_side_on_is_untouched(self):
        self.assertEqual(_f3("F.faceOn(1, 0, 0, 0.6, [])"), [1, 0, 0])

    def test_end_on_turns_toward_the_screen_keeping_its_lean(self):
        x, y, z = _f3("F.faceOn(0.05, 0.1, -0.9937, 0.6, [])")
        self.assertAlmostEqual(math.hypot(x, y), 0.6, places=6)
        self.assertAlmostEqual(math.hypot(x, y, z), 1, places=6)
        self.assertAlmostEqual(y / x, 2, places=6)              # same on-screen direction
        self.assertLess(z, 0)                                   # still away from the camera

    def test_dead_on_goes_up_the_screen(self):
        x, y, z = _f3("F.faceOn(0, 0, -1, 0.6, [])")
        self.assertAlmostEqual(x, 0)
        self.assertAlmostEqual(y, 0.6)

    def test_reuses_its_output(self):
        self.assertTrue(_f3("(() => { const o = [0, 0, 0]; return F.faceOn(0, 0, 1, 0.6, o) === o; })()"))


# ---- the wiring ---------------------------------------------------------------------

class TestWiring(unittest.TestCase):
    def setUp(self):
        self.home = (UI / "home.js").read_text()
        self.f3 = (UI / "field3d.js").read_text()

    def test_the_viewer_takes_acts_and_applies_the_pose(self):
        self.assertIn("  selfActs(", self.f3)
        self.assertIn("posesAt(a.acts, now, rm, a.pose)", self.f3)
        self.assertIn("bodyShape(", self.f3.split("_stepSelf(now) {", 1)[1])
        # the cycle frame feeds the move
        self.assertIn("amoebaActs({ kind: \"cycle\"", self.f3)

    def test_home_feeds_fresh_lines_only(self):
        body = self.home.split("THE BRAIN ON THE SCREEN", 1)[1].split("// ---- end of the brain", 1)[0]
        self.assertIn("v.selfActs(", body)
        self.assertIn("actableLine(", body)

    def test_the_docs_have_the_table(self):
        doc = (ROOT / "docs" / "selfgrowth.md").read_text()
        sec = doc.split("## Watching it grow", 1)[1]
        self.assertIn("### The amoeba acts it out", sec)
        for want in ("stretch", "pseudopod", "jolt", "startle", "breath", "ripple", "bounce", "droop", "wiggle", "glow"):
            self.assertIn(want, sec.split("### The amoeba acts it out", 1)[1], want)


def _home(expr):
    js = ('const H=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "home.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestActableLine(unittest.TestCase):
    def test_only_lines_near_the_newest_are_acted(self):
        # a page back in view catches up on the record: only the lines of the
        # last moments are acted out, never a replay of the backlog
        self.assertTrue(_home("H.actableLine({ts: 1000}, 1010)"))
        self.assertFalse(_home("H.actableLine({ts: 900}, 1010)"))
        self.assertFalse(_home("H.actableLine({}, 1010)"))


if __name__ == "__main__":
    unittest.main()
