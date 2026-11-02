"""Idle avatars park on a non-overlapping golden-angle spiral (orb width 46)."""
import re
import subprocess
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "field3d.js").read_text()


class IdleLayout(unittest.TestCase):
    def test_no_overlap_up_to_40(self):
        m = re.search(r"function f3IdleSlot\(n\) \{.*?\n\}\n", SRC, re.S)
        self.assertTrue(m, "f3IdleSlot missing")
        js = ("const F3_WORLD=1000;" + m.group(0) +
              "console.log(JSON.stringify(Array.from({length:40},(_,i)=>f3IdleSlot(i))));")
        import json
        pts = json.loads(subprocess.check_output(["node", "-e", js], text=True))
        bad = [(i, j) for i in range(len(pts)) for j in range(i + 1, len(pts))
               if ((pts[i][0]-pts[j][0])**2 + (pts[i][2]-pts[j][2])**2) ** .5 < 46]
        self.assertFalse(bad, f"{len(bad)} overlapping pairs, e.g. {bad[:3]}")


if __name__ == "__main__":
    unittest.main()
