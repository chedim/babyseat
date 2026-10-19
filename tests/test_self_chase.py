"""#161: the camera follows self from BEHIND -- from where it was a few cycles
ago, slightly above, looking the way it is heading (the operator: "can the
camera follow self from behind, from its past positions?")."""
import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def _chase(trail):
    js = ('const H=require(%r); process.stdout.write(JSON.stringify(H.chaseAngles(%s)))'
          % (str(UI / "home.js"), json.dumps(trail)))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestTheChaseAngles(unittest.TestCase):
    def test_moving_along_plus_x_the_camera_sits_behind_on_minus_x_and_above(self):
        a = _chase([[float(i), 0.0, 0.0] for i in range(8)])
        # camera direction from the target: (sin phi sin theta, cos phi, sin phi cos theta)
        d = (math.sin(a["phi"]) * math.sin(a["theta"]), math.cos(a["phi"]),
             math.sin(a["phi"]) * math.cos(a["theta"]))
        self.assertLess(d[0], -0.8)            # behind
        self.assertGreater(d[1], 0.1)          # a little above
        self.assertAlmostEqual(d[2], 0.0, places=6)

    def test_too_short_or_still_a_trail_gives_nothing(self):
        self.assertIsNone(_chase([[0, 0, 0]]))
        self.assertIsNone(_chase([[1, 1, 1]] * 6))


class TestTheWiring(unittest.TestCase):
    def test_the_viewer_hands_out_its_trail_and_home_chases_it(self):
        self.assertIn("  selfTrail() {", (UI / "field3d.js").read_text())
        home = (UI / "home.js").read_text()
        # ◎ self: toward its fresh look, else the chase from behind
        self.assertIn("selfCamAngles({ pos: sp, look, trail: tr })", home)
        self.assertIn("|| chaseAngles(st && st.trail)", home)
