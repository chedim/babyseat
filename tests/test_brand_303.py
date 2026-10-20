"""THE BRAND IS BABYSEAT (#303). From 0.7 the whole project -- seat, web UI, self,
skill tree, toybox, HA integration, Docker image -- ships as **babyseat**; membrane
is its server part (the memory field + proxy). Brand only: the Python module
`membrane`, the `membrane` CLI, MEMBRANE_* env vars, .membrane dirs and the Home
Assistant domain `membrane` keep their names, so nobody's setup breaks.

  - PyPI: the distribution is `babyseat`, with a `babyseat` console script that is
    `membrane standalone`; `membrane` stays.
  - a last `membrane-seat` release is a SHIM (no code, depends on
    babyseat==<same version>) so `pip install -U membrane-seat` moves people over;
    the publish lane builds and uploads both, and refuses a shim whose version
    is not the package's.
  - Docker: the image lane pushes chedim/babyseat AND chedim/membrane.
  - HA: the manifest requires babyseat==X; the domain stays `membrane`.
  - the updater and the asset fetcher read the installed version under the new
    name (falling back to the old one).
"""

import json
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SHIM = ROOT / "shim" / "membrane-seat" / "pyproject.toml"


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


class TestTheDistributionIsBabyseat(unittest.TestCase):
    def test_pyproject_names_babyseat(self):
        self.assertEqual(_project()["name"], "babyseat")

    def test_both_console_scripts(self):
        scripts = _project()["scripts"]
        self.assertEqual(scripts["membrane"], "membrane.cli:main")
        self.assertEqual(scripts["babyseat"], "membrane.cli:babyseat_main")

    def test_the_module_keeps_its_name(self):
        cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(cfg["tool"]["setuptools"]["packages"], ["membrane"])


class TestTheShim(unittest.TestCase):
    def setUp(self):
        self.cfg = tomllib.loads(SHIM.read_text())
        self.proj = self.cfg["project"]

    def test_it_is_membrane_seat_at_the_same_version(self):
        self.assertEqual(self.proj["name"], "membrane-seat")
        self.assertEqual(self.proj["version"], _project()["version"])

    def test_it_depends_on_babyseat_at_exactly_that_version(self):
        self.assertEqual(self.proj["dependencies"], [f"babyseat=={_project()['version']}"])

    def test_it_ships_no_code(self):
        st = self.cfg["tool"]["setuptools"]
        self.assertEqual(st.get("packages"), [])
        self.assertEqual(st.get("py-modules"), [])
        self.assertNotIn("scripts", self.proj)

    def test_its_readme_and_license_are_its_own_files(self):
        here = SHIM.parent
        self.assertTrue((here / self.proj["readme"]).is_file())
        for lf in self.proj["license-files"]:
            self.assertTrue((here / lf).is_file(), lf)


class TestThePublishLane(unittest.TestCase):
    def setUp(self):
        self.wf = (ROOT / ".github" / "workflows" / "publish.yml").read_text()

    def test_it_builds_both_distributions_into_one_dist(self):
        self.assertIn("python -m build --outdir dist", self.wf)
        self.assertIn("python -m build shim/membrane-seat --outdir dist", self.wf)
        self.assertIn("packages-dir: dist", self.wf)

    def test_it_refuses_a_shim_that_is_not_the_version(self):
        self.assertIn("shim/membrane-seat/pyproject.toml", self.wf)
        self.assertRegex(self.wf, r'\[ "\$shim" = "\$ver" \]')
        self.assertIn('babyseat==$ver', self.wf)


class TestTheImageLane(unittest.TestCase):
    def test_it_pushes_both_images(self):
        wf = (ROOT / ".github" / "workflows" / "docker.yml").read_text()
        self.assertIn("chedim/babyseat", wf)
        self.assertIn("chedim/membrane", wf)
        self.assertRegex(wf, r"images: \|\s+chedim/babyseat\s+chedim/membrane")

    def test_the_image_title_is_babyseat(self):
        self.assertIn('org.opencontainers.image.title="babyseat"',
                      (ROOT / "Dockerfile").read_text())


class TestTheHomeAssistantIntegration(unittest.TestCase):
    def setUp(self):
        self.man = json.loads((ROOT / "custom_components" / "membrane" / "manifest.json").read_text())

    def test_it_requires_babyseat(self):
        v = _project()["version"]
        self.assertEqual(self.man["requirements"], [f"babyseat=={v}"])

    def test_the_domain_stays_membrane(self):
        self.assertEqual(self.man["domain"], "membrane")

    def test_the_names_say_babyseat(self):
        self.assertEqual(self.man["name"], "babyseat (membrane)")
        hacs = json.loads((ROOT / "hacs.json").read_text())
        self.assertEqual(hacs["name"], "babyseat (membrane)")


class TestTheBabyseatCommand(unittest.TestCase):
    def _run(self, argv):
        from membrane import cli
        seen = {}

        def fake_main():
            seen["argv"] = list(sys.argv)
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(cli, "main", fake_main):
            cli.babyseat_main()
        return seen.get("argv")

    def test_bare_is_membrane_standalone(self):
        self.assertEqual(self._run(["babyseat"]), ["babyseat", "standalone"])

    def test_flags_go_to_standalone(self):
        self.assertEqual(self._run(["babyseat", "--no-open", "--serve-port", "4100"]),
                         ["babyseat", "standalone", "--no-open", "--serve-port", "4100"])

    def test_help_prints_and_boots_nothing(self):
        from io import StringIO
        for h in ("-h", "--help"):
            out = StringIO()
            with mock.patch("sys.stdout", out):
                self.assertIsNone(self._run(["babyseat", h]))
            self.assertIn("membrane standalone", out.getvalue())
            self.assertIn("babyseat", out.getvalue())


class TestTheInstalledVersionUnderTheNewName(unittest.TestCase):
    def test_the_updater_follows_babyseat(self):
        from membrane import update
        self.assertEqual(update.DIST, "babyseat")
        self.assertEqual(update.PYPI_JSON, "https://pypi.org/pypi/babyseat/json")

    def _fake_version(self, have):
        from importlib.metadata import PackageNotFoundError

        def version(name):
            if name in have:
                return have[name]
            raise PackageNotFoundError(name)
        return version

    def test_the_updater_reads_babyseat_then_membrane_seat(self):
        from membrane import update
        with mock.patch("importlib.metadata.version",
                        self._fake_version({"babyseat": "0.7.0", "membrane-seat": "0.7.0"})):
            self.assertEqual(update._pypi_installed(), "0.7.0")
        with mock.patch("importlib.metadata.version",
                        self._fake_version({"membrane-seat": "0.6.2"})):
            self.assertEqual(update._pypi_installed(), "0.6.2")

    def test_the_assets_follow_babyseat(self):
        from membrane import assets
        with mock.patch("importlib.metadata.version",
                        self._fake_version({"babyseat": "0.7.0"})):
            self.assertEqual(assets._version(), "0.7.0")
        with mock.patch("importlib.metadata.version",
                        self._fake_version({"membrane-seat": "0.6.2"})):
            self.assertEqual(assets._version(), "0.6.2")


class TestTheInstaller(unittest.TestCase):
    def test_install_sh_asks_for_babyseat_by_its_name(self):
        sh = (ROOT / "install.sh").read_text()
        self.assertIn('PIP_SPEC="babyseat @ git+$REPO_URL"', sh)


class TestTheWordForTheLLMsRole(unittest.TestCase):
    def test_no_babysitter(self):
        hits = []
        for p in [ROOT / "README.md", ROOT / "doctrine.md", *ROOT.glob("docs/**/*.md"),
                  *ROOT.glob("membrane/**/*.py"), *ROOT.glob("membrane/ui/*.js"),
                  *ROOT.glob("membrane/ui/*.html")]:
            try:
                if "babysitter" in p.read_text(errors="ignore").lower():
                    hits.append(str(p.relative_to(ROOT)))
            except OSError:
                pass
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
