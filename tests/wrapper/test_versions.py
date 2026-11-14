"""THE VERSION IS ONE VERSION. Every place that names the version names the same one: pyproject, the Home Assistant manifest
(connect-only: it pins no package), the README's status line, the newest CHANGELOG heading, CITATION.cff -- and the publish
lane refuses a tag that is not the package's version. The names a user types (`membrane`, `babyseat`), the `membrane-seat`
shim's pin and the Home Assistant domain are pinned here too (#303, #1143)."""

import json
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def _pyproject_version():
    return _project()["version"]


class TestTheVersionIsOneVersion(unittest.TestCase):
    def test_the_ha_manifest_is_a_connect_only_client_at_this_version_or_ahead(self):
        # #1143: the component installs no package (it connects to a seat the user runs), so nothing pins babyseat; its version is the
        # release's (0.7.60), which pyproject reaches at the release bump: never behind it
        man = json.loads((ROOT / "custom_components" / "membrane" / "manifest.json").read_text())
        self.assertEqual(man["requirements"], [])
        ver = lambda v: tuple(int(x) for x in v.split("."))
        self.assertGreaterEqual(ver(man["version"]), ver(_pyproject_version()))

    def test_the_ha_domain_stays_membrane(self):
        man = json.loads((ROOT / "custom_components" / "membrane" / "manifest.json").read_text())
        self.assertEqual(man["domain"], "membrane")

    def test_the_readme_says_it(self):
        m = re.search(r"Version \*\*([0-9.]+)\*\*", (ROOT / "README.md").read_text())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), _pyproject_version())

    def test_the_newest_changelog_entry_is_it(self):
        m = re.search(r"(?m)^## v?([0-9]+\.[0-9]+\.[0-9]+)", (ROOT / "CHANGELOG.md").read_text())
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), _pyproject_version())

    def test_the_citation_names_a_release_of_this_line(self):
        # CITATION.cff is what GitHub's "Cite this repository" and Zenodo read. On a release branch it names exactly the package's
        # version; on main it may name the newest patch release of the same line, never an older one.
        cff = (ROOT / "CITATION.cff").read_text()
        m = re.search(r'(?m)^version: "?([0-9]+\.[0-9]+\.[0-9]+)"?\s*$', cff)
        self.assertIsNotNone(m)
        cite = tuple(int(x) for x in m.group(1).split("."))
        pkg = tuple(int(x) for x in _pyproject_version().split("."))
        self.assertEqual(cite[:2], pkg[:2])
        self.assertGreaterEqual(cite, pkg)
        lic = _project()["license"]
        self.assertRegex(cff, rf"(?m)^license: \"?{re.escape(lic)}\"?\s*$")

    def test_the_publish_lane_refuses_a_tag_that_is_not_the_version(self):
        wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text()
        self.assertIn("tomllib", wf)
        self.assertRegex(wf, r'\[ "\$\{tag#v\}" = "\$ver" \]')

    def test_both_console_scripts_keep_their_names(self):
        self.assertEqual(set(_project()["scripts"]), {"membrane", "babyseat"})

    def test_the_membrane_seat_shim_pins_this_version(self):
        shim = tomllib.loads((ROOT / "shim" / "membrane-seat" / "pyproject.toml").read_text())["project"]
        self.assertEqual(shim["name"], "membrane-seat")
        self.assertEqual(shim["version"], _pyproject_version())
        self.assertEqual(shim["dependencies"], [f"babyseat=={_pyproject_version()}"])


if __name__ == "__main__":
    unittest.main()
