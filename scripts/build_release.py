#!/usr/bin/env python3
"""THE PRIVATE BETA'S ARTIFACTS (2026-10-12, the operator: "ready for a private
beta testing by those who I give access to membrane repo on gh"): the repo is
private, so neither HACS nor PyPI nor the release-asset downloads reach a tester.
Instead:

  dist/membrane-ha-<version>.zip     unzip into <config>/custom_components/ --
                                     the integration with the membrane package
                                     and the voice files VENDORED inside
                                     (custom_components/membrane/vendor/): no
                                     PyPI, no downloads; numpy is HA's own
  dist/membrane-voice-<version>.zip  the voice files for a standalone install
                                     (unzip into <seat>/.membrane/assets/)
  dist/membrane-standalone-<version>.zip
                                     a standalone installer: the wheel + the
                                     voice files, for testers who can't or
                                     won't pip from a private repo

    python scripts/build_release.py
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CC = ROOT / "custom_components" / "membrane"
PKG = ROOT / "membrane"
WWW = ROOT / "integrations" / "homeassistant" / "custom_components" / "ai_terminal" / "www"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))
from membrane.assets import MANIFEST  # noqa: E402


def version() -> str:
    return re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)


def _skip(p: Path) -> bool:
    return "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo")


def main() -> int:
    v = version()
    DIST.mkdir(exist_ok=True)
    missing = [n for n in MANIFEST if not (WWW / n).is_file()]
    if missing:
        print(f"voice files missing in {WWW}: {missing}", file=sys.stderr)
        return 1
    ha = DIST / f"membrane-ha-{v}.zip"
    with zipfile.ZipFile(ha, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(CC.rglob("*")):
            if p.is_file() and not _skip(p):
                arc = Path("membrane") / p.relative_to(CC)
                if p.name == "manifest.json":
                    m = json.loads(p.read_text())
                    m["requirements"] = []              # vendored: HA installs nothing
                    z.writestr(str(arc), json.dumps(m, indent=2) + "\n")
                else:
                    z.write(p, str(arc))
        for p in sorted(PKG.rglob("*")):
            if p.is_file() and not _skip(p):
                z.write(p, str(Path("membrane/vendor/membrane") / p.relative_to(PKG)))
        for n in MANIFEST:
            z.write(WWW / n, f"membrane/vendor/assets/{n}")
    voice = DIST / f"membrane-voice-{v}.zip"
    with zipfile.ZipFile(voice, "w", zipfile.ZIP_STORED) as z:
        for n in MANIFEST:
            z.write(WWW / n, n)
    # the standalone installer: the wheel (pure python, no build step for the
    # tester) + the voice files, unzipped anywhere pip-installable
    wheel = next(DIST.glob(f"membrane_seat-{v}-*.whl"), None)
    if wheel is None:
        print(f"building the wheel into {DIST} ...")
        import subprocess
        venv = ROOT / "venv" / "bin" / "python"
        if not venv.is_file():
            venv = ROOT / ".venv" / "bin" / "python"
        py = str(venv) if venv.is_file() else sys.executable
        cmd = [py, "-m", "build", "--wheel", "--outdir", str(DIST)]
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        wheel = next(DIST.glob(f"membrane_seat-{v}-*.whl"), None)
        if wheel is None:                       # no `build`: pip wheel works the same
            r = subprocess.run([py, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                                "--wheel-dir", str(DIST), str(ROOT)],
                               cwd=ROOT, capture_output=True, text=True)
            wheel = next(DIST.glob(f"membrane_seat-{v}-*.whl"), None)
        if wheel is None:
            print((r.stdout or "")[-1500:], file=sys.stderr)
            print((r.stderr or "")[-1500:], file=sys.stderr)
            print("the wheel build failed", file=sys.stderr)
            return 1
    INSTALL = "\n".join([
        f"membrane standalone installer {v}",
        "",
        "1. pip install ./" + wheel.name,
        "2. mkdir ~/bob && cd ~/bob && membrane standalone   # opens http://localhost:4096/",
        "3. copy the zip's assets/ into ~/bob/.membrane/  (you get ~/bob/.membrane/assets/)",
        "",
        "Details: BETA.md and docs/standalone.md on the release page.",
    ]) + "\n"
    st = DIST / f"membrane-standalone-{v}.zip"
    with zipfile.ZipFile(st, "w", zipfile.ZIP_STORED) as z:
        z.writestr("INSTALL.txt", INSTALL)
        z.write(wheel, wheel.name)
        for n in MANIFEST:
            with (WWW / n).open("rb") as f:
                z.writestr(f"assets/{n}", f.read())
    for f in (ha, voice, st):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
