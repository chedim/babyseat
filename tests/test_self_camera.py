"""◎ self's camera stays close, follows and looks (after #237).

The operator, on #237's ◎ self: "camera is too far and not really
following/looking". The frame fitted ALL 8 looks of the trail (spread over the
whole field, so the distance sat near the 1400 cap), the target only moved
after 30 s without a touch, and the angle chased self's past, never what it
looks at now.

Now: the frame is the amoeba + its CURRENT look (fresh, like 👁's
EYES_FRESH_MS) + its aim + its last 2 steps; the distance is clamped to a
close [FRAME_MIN, FRAME_MAX]; ◎ self follows at once (a touch holds only a
few seconds) at a snappier rate; and the camera sits behind self looking
along self -> its look (the chase angle when there is no fresh look).

The pure logic lives in home.js (HomeLogic) and is run here with node.
"""
import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"


def _js(expr):
    js = ('const H=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / "home.js"), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _cam(pos, ang, r=100):
    """Where field3d's orbit puts the camera for these angles."""
    ph, th = ang["phi"], ang["theta"]
    return [pos[0] + r * math.sin(ph) * math.sin(th), pos[1] + r * math.cos(ph), pos[2] + r * math.sin(ph) * math.cos(th)]


@NODE
class TestCloseFrame(unittest.TestCase):
    def test_stale_trail_looks_do_not_widen_the_frame(self):
        st = {"pos": [0, 0, 0],
              "looks": [[900, 0, 0], [0, -800, 0], [0, 0, 700]],        # the old trail, all over the field
              "look": {"p": [60, 0, 0], "age": 1000},                    # what it looks at now
              "aim": [0, 30, 0], "steps": [[5, 0, 0], [8, 0, 0]]}
        self.assertEqual(_js("H.selfFrame(%s).radius" % json.dumps(st)), 60)

    def test_a_stale_current_look_is_ignored_too(self):
        st = {"pos": [0, 0, 0], "look": {"p": [600, 0, 0], "age": 60000}, "aim": [0, 30, 0]}
        self.assertEqual(_js("H.selfFrame(%s).radius" % json.dumps(st)), 30)

    def test_only_the_last_couple_of_steps_count(self):
        st = {"pos": [0, 0, 0], "steps": [[400, 0, 0], [300, 0, 0], [20, 0, 0], [10, 0, 0]]}
        self.assertEqual(_js("H.selfFrame(%s).radius" % json.dumps(st)), 20)

    def test_the_distance_is_clamped_close(self):
        mx = _js("H.frameDistance(1e9)")
        self.assertLessEqual(mx, 520)
        self.assertGreaterEqual(mx, 400)
        mn = _js("H.frameDistance(0)")
        self.assertGreaterEqual(mn, 55)
        self.assertLessEqual(mn, 85)
        # a typical look 150 away: well under the old ~600
        self.assertLess(_js("H.frameDistance(150)"), 400)


@NODE
class TestFollowsAtOnce(unittest.TestCase):
    def test_self_mode_is_not_idle_gated(self):
        # 5 s after a touch ◎ self is back; the other modes still wait 30 s
        self.assertTrue(_js("H.followsNow('self', 5000)"))
        self.assertFalse(_js("H.followsNow('seat', 5000)"))
        self.assertFalse(_js("H.followsNow('activity', 5000)"))
        self.assertTrue(_js("H.followsNow('seat', 31000)"))

    def test_a_touch_holds_self_only_briefly(self):
        self.assertFalse(_js("H.followsNow('self', 500)"))           # your hand on the camera
        self.assertLessEqual(_js("H.SELF_HOLD_MS"), 6000)
        self.assertTrue(_js("H.followsNow('self', H.SELF_HOLD_MS)"))

    def test_self_follows_snappier(self):
        s, o = _js("H.followRates('self')"), _js("H.followRates('seat')")
        for k in ("target", "turn", "zoom"):
            self.assertGreater(s[k], o[k], k)
        self.assertGreaterEqual(s["target"], 2.5)


@NODE
class TestLooksWhereSelfLooks(unittest.TestCase):
    def test_behind_self_looking_along_self_to_its_look(self):
        pos, look = [10, 0, 0], [110, 0, 0]
        a = _js("H.lookAngles(%s, %s)" % (json.dumps(pos), json.dumps(look)))
        c = _cam(pos, a)
        d = [look[i] - pos[i] for i in range(3)]
        off = [c[i] - pos[i] for i in range(3)]
        n = math.hypot(*off)
        along = sum(off[i] * d[i] for i in range(3)) / (n * math.hypot(*d))
        self.assertLess(along, -0.8)                     # behind self, opposite the look
        self.assertGreater(off[1], 0)                    # and a little above

    def test_no_angle_on_top_of_self(self):
        self.assertIsNone(_js("H.lookAngles([0,0,0], [0,0,0.5])"))

    def test_fresh_look_wins_else_the_chase(self):
        trail = [[0, 0, 0], [0, 0, 50], [0, 0, 100]]
        fresh = {"pos": [0, 0, 100], "look": {"p": [100, 0, 100], "age": 500}, "trail": trail}
        stale = dict(fresh, look={"p": [100, 0, 100], "age": 60000})
        self.assertEqual(_js("H.selfCamAngles(%s)" % json.dumps(fresh)),
                         _js("H.lookAngles([0,0,100],[100,0,100])"))
        self.assertEqual(_js("H.selfCamAngles(%s)" % json.dumps(stale)),
                         _js("H.chaseAngles(%s)" % json.dumps(trail)))
        self.assertIsNone(_js("H.selfCamAngles({pos:[0,0,0]})"))


if __name__ == "__main__":
    unittest.main()
