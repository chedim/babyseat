"""help('map') tells a map-mode seat that the banner is off, where it still lives and which dials it has
(operator 2026-11-11: the bare banner). Every dial it names must exist in the banner pack, so the text cannot drift."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HELP = (ROOT / "membrane" / "help" / "map.md").read_text(encoding="utf-8")
PACK = (ROOT / "membrane" / "packs" / "banner.c1f").read_text(encoding="utf-8")


class TestMapHelpBanner(unittest.TestCase):
    def _section(self) -> str:
        m = re.search(r"^## The banner.*?(?=^## )", HELP, re.S | re.M)
        self.assertIsNotNone(m, "help('map') has no banner section")
        return m.group(0)

    def test_the_section_says_where_the_banner_lives(self):
        sec = self._section()
        self.assertIn("Banner:<owner>:<chat>", sec)
        self.assertIn("entity edit", sec)

    def test_every_dial_it_names_exists_in_the_pack(self):
        sec = self._section()
        dials = re.findall(r"`([a-z_]+)`", sec)
        named = [d for d in dials if d not in ("entity",)]
        self.assertTrue(named)
        for d in named:
            self.assertIn(d, PACK, f"help('map') names a banner dial the pack does not have: {d}")


if __name__ == "__main__":
    unittest.main()
