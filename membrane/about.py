"""The About window's data (the operator, 2026-11-10): the seat's version, its git short sha (a checkout; none for an
installed package) and the changelog -- one About:seat entity the about pack (membrane/packs/about.c1f) draws in the
page. Data only: the window, its rows and the open / close are the pack's."""
from __future__ import annotations

import subprocess
from pathlib import Path

EID = "About:seat"
CLASS = "About"
PKG = Path(__file__).resolve().parent
CHANGELOG = PKG / "CHANGELOG.md"


def _sha() -> str | None:
    root = PKG.parent
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=5)
        return out.stdout.strip() or None
    except Exception:                                    # noqa: BLE001 -- no git: no sha
        return None


def read() -> dict:
    from .update import installed_version
    try:
        log = CHANGELOG.read_text()
    except OSError:
        log = ""
    return {"version": installed_version(), "sha": _sha(), "changelog": log}


def install(est) -> None:
    """At boot: the About:seat entity carries this seat's version, sha and changelog."""
    from .entities import QuantumEntity
    a = read()
    d = (est.docs or {}).get(EID)
    props = {"name": "about this seat", "silent_birth": True, **a}
    if d is None:
        est.upsert(QuantumEntity(eid=EID, cls=CLASS, properties=props), embedder=None)
    elif any((d.properties or {}).get(k) != v for k, v in a.items()):
        d.properties = {**(d.properties or {}), **a}
        est.upsert(d, embedder=None)
