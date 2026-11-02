"""F3Logic.markerZ(kind): stacking order of work markers, goal > decision > wait > task."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")


def _z(kind):
    js = ('const F=require(%r); process.stdout.write(JSON.stringify(F.markerZ(%s)))'
          % (str(UI / "field3d.js"), json.dumps(kind)))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestMarkerZ(unittest.TestCase):
    def test_strict_order_integers(self):
        zs = [_z(k) for k in ("goal", "decision", "wait", "task")]
        for z in zs:
            self.assertIsInstance(z, int)
        self.assertTrue(zs[0] > zs[1] > zs[2] > zs[3], zs)


if __name__ == "__main__":
    unittest.main()
