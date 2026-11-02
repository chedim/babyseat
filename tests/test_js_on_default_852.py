"""JS scripting in the window is ON by default (#852): no 'off by default' text."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PANEL = (ROOT / "membrane" / "ui" / "panel.js").read_text()
JSEXEC = (ROOT / "membrane" / "jsexec.py").read_text()


class JsOnDefault(unittest.TestCase):
    def test_js_on_defaults_true(self):
        self.assertRegex(PANEL, r"this\._jsOn\s*=\s*true\s*;")

    def test_no_off_by_default_text(self):
        self.assertNotRegex(PANEL, r"(?i)off by default")
        self.assertNotRegex(JSEXEC, r"(?i)off by default")


if __name__ == "__main__":
    unittest.main()
