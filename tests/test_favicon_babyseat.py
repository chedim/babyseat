"""#161: the tab's icon is the babyseat (formerly membrane's turning cube) -- an
infant car seat with self, the glowing teal amoeba, strapped in. The static
favicon.svg is the seat; favicon.js draws the same seat on a canvas, rocking a
little on its base with the amoeba's glow breathing, still when the person
asks for reduced motion, and asleep while the tab is hidden.

The wiring is asserted on the source (like the other UI tests)."""
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
SVG_NS = "{http://www.w3.org/2000/svg}"


class FaviconSvg(unittest.TestCase):
    def setUp(self):
        self.src = (UI / "favicon.svg").read_text()
        self.root = ET.fromstring(self.src)          # valid XML, or this raises

    def test_is_an_svg_square_and_small(self):
        self.assertEqual(self.root.tag, SVG_NS + "svg")
        vb = [float(v) for v in self.root.get("viewBox").split()]
        self.assertEqual(vb[2], vb[3])                # square: a tab icon
        self.assertLess(len(self.src), 4096)

    def test_the_amoeba_is_in_the_seat(self):
        grads = [g.get("id") for g in self.root.iter(SVG_NS + "radialGradient")]
        self.assertIn("amoeba", grads)
        self.assertTrue(any(e.get("fill") == "url(#amoeba)" for e in self.root.iter()))
        self.assertIn("#ffd166", self.src)             # the chest strap and buckle
        self.assertGreaterEqual(len(list(self.root.iter(SVG_NS + "path"))), 3)  # handle, shell, rocker

    def test_transparent_and_no_cube(self):
        self.assertNotIn("<rect width=\"100%\"", self.src)  # no background plate
        self.assertNotIn("#b07cff", self.src)             # the cube's purple gradient is gone
        self.assertNotIn("M32 8 L54 20", self.src)


class FaviconJs(unittest.TestCase):
    def setUp(self):
        self.src = (UI / "favicon.js").read_text()

    def test_the_cube_is_gone(self):
        for word in ("CORNERS", "EDGES", "yaw", "pitch"):
            self.assertNotIn(word, self.src)

    def test_draws_the_seat_svg(self):
        self.assertIn("export function startFavicon", self.src)
        self.assertIn("favicon.svg", self.src)
        self.assertIn("babyseat", self.src.lower())

    def test_honours_reduced_motion(self):
        self.assertIn("prefers-reduced-motion", self.src)

    def test_sleeps_while_hidden(self):
        self.assertIn("visibilitychange", self.src)
        self.assertIn("document.hidden", self.src)
        self.assertIn("clearInterval", self.src)

    def test_fps_stays_low(self):
        m = re.search(r"FPS\s*=\s*(\d+)", self.src)
        self.assertIsNotNone(m)
        self.assertLessEqual(int(m.group(1)), 12)

    def test_index_comment_no_longer_says_cube(self):
        line = [l for l in (UI / "index.html").read_text().splitlines() if "favicon.js" in l][0]
        self.assertNotIn("cube", line)


if __name__ == "__main__":
    unittest.main()
