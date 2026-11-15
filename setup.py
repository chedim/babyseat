"""The platform wheel's build hook (#1143, docs/packaging-plan.md).

Everything about the package is in pyproject.toml. This file only turns a PREPARED PAYLOAD into the binary wheel: cargo never runs here (the dist folder is built by
scripts/dist/build.sh, packed by scripts/dist/assemble_wheel_payload.sh; scripts/dist/build_wheel.sh runs the whole step).

  BABYSEAT_PAYLOAD=<payload dir>        the folder that holds `dist/` (bin/cliffvm packs/ apps/ help/ VERSION MANIFEST.sha256 ...): copied to `membrane/dist/` in the wheel
  BABYSEAT_PLATFORM_TAG=<platform tag>  e.g. macosx_11_0_arm64, manylinux_2_28_x86_64, musllinux_1_2_aarch64: the wheel is `py3-none-<tag>` (no Python ABI: the binary is not an extension)

No payload means NO WHEEL: building a wheel from the sdist (or from a checkout) stops with "no binary for this platform", so a platform nobody built for never installs a launcher
with nothing to launch (decision 4, 2026-11-14). The sdist itself carries the three launcher files and no binary."""
import os
import re
import shutil
import sys
from pathlib import Path

from setuptools import Distribution, setup
from setuptools.command.bdist_wheel import bdist_wheel
from setuptools.command.build_py import build_py

PAYLOAD = os.environ.get("BABYSEAT_PAYLOAD", "")
PLATFORM = os.environ.get("BABYSEAT_PLATFORM_TAG", "")


def _refuse(why: str) -> None:
    sys.exit(f"babyseat: no binary for this platform: {why}. A wheel carries the prebuilt `cliffvm` seat and is built by scripts/dist/build_wheel.sh <platform tag> "
             "from a dist folder (scripts/dist/build.sh); the source distribution carries no binary and cannot be installed. "
             "Install a platform wheel (pip install babyseat on macOS or Linux, x86_64 or arm64) or the release tarball (install.sh).")


def _payload() -> Path:
    if not PAYLOAD or not PLATFORM:
        _refuse("BABYSEAT_PAYLOAD / BABYSEAT_PLATFORM_TAG are not set")
    if not re.fullmatch(r"[a-z0-9_]+", PLATFORM):
        _refuse(f"{PLATFORM!r} is not a wheel platform tag")
    d = Path(PAYLOAD) / "dist"
    if not (d / "bin" / "cliffvm").is_file():
        _refuse(f"{d}/bin/cliffvm does not exist")
    return d


class BuildPy(build_py):
    def run(self):
        super().run()
        d = _payload()
        out = Path(self.build_lib) / "membrane" / "dist"
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(d, out)  # copy2: the executable bit of bin/cliffvm rides along into the wheel's zip


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True  # the wheel holds a platform binary: platlib, Root-Is-Purelib: false (there is no extension to compile)


class BdistWheel(bdist_wheel):
    def finalize_options(self):
        _payload()
        super().finalize_options()

    def get_tag(self):
        return ("py3", "none", PLATFORM)


setup(distclass=BinaryDistribution, cmdclass={"build_py": BuildPy, "bdist_wheel": BdistWheel})
