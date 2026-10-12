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
    for f in (ha, voice):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
