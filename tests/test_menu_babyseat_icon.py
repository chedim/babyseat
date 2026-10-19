"""#161: the top bar's menu button wears the babyseat (the tab's icon) instead
of the ☰ glyph -- the operator: "replace the burger menu with our babyseat
icon/logo". The image loads standalone (/ui/www/) and inside Home Assistant
(/api/membrane/www/)."""
import re
import unittest
from pathlib import Path

HOME = (Path(__file__).resolve().parents[1] / "membrane" / "ui" / "home.js").read_text()


class TestMenuIcon(unittest.TestCase):
    def button(self):
        m = re.search(r'<button class="ha"[^>]*>(.*?)</button>', HOME, re.S)
        self.assertIsNotNone(m, "the menu button is gone")
        return m.group(0), m.group(1)

    def test_the_burger_is_the_babyseat(self):
        tag, inner = self.button()
        self.assertNotIn("☰", inner)
        self.assertIn("<img", inner)
        self.assertIn("seat-logo", inner)

    def test_the_icon_loads_standalone_and_in_home_assistant(self):
        self.assertIn('"/ui/www/favicon.svg"', HOME)
        self.assertIn('"/api/membrane/www/favicon.svg"', HOME)
        self.assertNotIn("import.meta", HOME)          # the tests require() home.js

    def test_still_labelled_for_screen_readers(self):
        tag, inner = self.button()
        self.assertIn('aria-label="Home Assistant menu"', tag)
        self.assertRegex(inner, r'alt=""')


if __name__ == "__main__":
    unittest.main()
