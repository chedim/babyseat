"""Runs the panel's JS logic tests (tests/js, node:test) inside the Python
suite -- skipped when node is absent (the brain phone has none)."""
import shutil
import subprocess
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parent / "js"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class TestPanelJs(unittest.TestCase):
    def test_node_suite(self):
        files = sorted(str(p) for p in JS.glob("*.test.cjs"))
        self.assertTrue(files)
        r = subprocess.run(["node", "--test", *files], capture_output=True,
                           text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
