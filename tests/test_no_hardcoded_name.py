"""NO HARD-CODED SEAT NAME (issue #6): the seat's name lives in settings.json
(naming.py, served by /ui/conf and HA's /api/membrane/conf). The boat's old
name must not survive anywhere in the code as a fallback, a wake word, a
label or a key -- a seat that wasn't named reads naming.DEFAULT."""
import re
import unittest
from pathlib import Path

from membrane import naming as N

ROOT = Path(__file__).resolve().parent.parent
TREES = (ROOT / "membrane", ROOT / "custom_components" / "membrane")
TEXT = {".py", ".js", ".html", ".json", ".md", ".css", ".svg"}
OLD = re.compile(r"argus|аргус|аргос|аргуш", re.I)


class TestNoHardcodedName(unittest.TestCase):
    def test_the_old_name_is_nowhere_in_the_code(self):
        hits = []
        for tree in TREES:
            for p in sorted(tree.rglob("*")):
                if p.is_file() and p.suffix in TEXT:
                    for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                        if OLD.search(line):
                            hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
        self.assertEqual(hits, [], "\n" + "\n".join(hits))

    def test_home_falls_back_to_the_servers_default_name(self):
        src = (ROOT / "membrane" / "ui" / "home.js").read_text()
        self.assertIn(f'window.MEMBRANE_NAME) || "{N.DEFAULT}";', src)


    def test_home_assistant_names_no_seat_itself(self):
        # one fallback, the seat's own default (const.DEFAULT_NAME mirrors
        # naming.DEFAULT: the integration runs without the membrane package)
        src = (TREES[1] / "const.py").read_text()
        self.assertRegex(src, r'(?m)^DEFAULT_NAME = "' + N.DEFAULT + '"')
        hits = [f"{p.name}:{i}: {line.strip()}" for p in sorted(TREES[1].glob("*.py")) if p.name != "const.py"
                for i, line in enumerate(p.read_text().splitlines(), 1) if '"' + N.DEFAULT + '"' in line]
        self.assertEqual(hits, [], "\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
