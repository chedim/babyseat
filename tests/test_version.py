"""THE VERSION IS ONE VERSION (2026-10-15). The v0.4.2 release published on GitHub
while pyproject.toml still said 0.4.1: the publish lane built membrane_seat-0.4.1 and
PyPI refused it (400, the file exists) -- v0.4.2 never reached PyPI. And the Home
Assistant manifest pinned membrane-seat==0.3.4: HA installs that package and serves
its UI from it, so the HA panel ran 0.3.4's code through every later release.

Every place that names the version names the same one: pyproject, the HA manifest's
version AND its requirement pin, the README's status line, the newest CHANGELOG
heading -- and the publish lane refuses a tag that is not the package's version."""

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


class TestTheVersionIsOneVersion(unittest.TestCase):
    def test_the_ha_manifest_ships_this_version_and_pins_it(self):
        v = _pyproject_version()
        man = json.loads((ROOT / "custom_components" / "membrane" / "manifest.json").read_text())
        self.assertEqual(man["version"], v)
        self.assertIn(f"babyseat=={v}", man["requirements"])   # #303: the dist is babyseat

    def test_the_readme_says_it(self):
        m = re.search(r"Version \*\*([0-9.]+)\*\*", (ROOT / "README.md").read_text())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), _pyproject_version())

    def test_the_newest_changelog_entry_is_it(self):
        m = re.search(r"(?m)^## v?([0-9]+\.[0-9]+\.[0-9]+)", (ROOT / "CHANGELOG.md").read_text())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), _pyproject_version())

    def test_the_citation_names_a_release_of_this_line(self):
        # #417: CITATION.cff is what GitHub's "Cite this repository" and Zenodo
        # read. On a release branch it names exactly the package's version; on
        # main it may name the newest patch release of the same line (main is
        # bumped only at the next minor), never an older one.
        cff = (ROOT / "CITATION.cff").read_text()
        m = re.search(r'(?m)^version: "?([0-9]+\.[0-9]+\.[0-9]+)"?\s*$', cff)
        self.assertIsNotNone(m)
        cite = tuple(int(x) for x in m.group(1).split("."))
        pkg = tuple(int(x) for x in _pyproject_version().split("."))
        self.assertEqual(cite[:2], pkg[:2])
        self.assertGreaterEqual(cite, pkg)
        lic = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["license"]
        self.assertRegex(cff, rf"(?m)^license: \"?{re.escape(lic)}\"?\s*$")

    def test_the_publish_lane_refuses_a_tag_that_is_not_the_version(self):
        wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
        self.assertIn("tomllib", wf)
        self.assertRegex(wf, r'\[ "\$\{tag#v\}" = "\$ver" \]')


if __name__ == "__main__":
    unittest.main()
