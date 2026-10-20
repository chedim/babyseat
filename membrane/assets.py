"""THE BIG VOICE FILES, FETCHED ON FIRST USE (2026-10-12): the on-device wake
word (Vosk's small English model) and the in-browser speech recognizer (the
ONNX runtime + transformers.js bundle) are ~65 MB -- too big for the package.
The first page that asks for one has the seat fetch it from the release that
matches this version, check its SHA-256, and keep it in <data dir>/assets
(Home Assistant: <config>/membrane/assets). Nothing runs that didn't match.

MEMBRANE_ASSETS_URL overrides the base (a mirror, an offline copy).
"""
from __future__ import annotations

import hashlib
import os
import threading
import urllib.request
from pathlib import Path

MANIFEST = {   # name: (sha256, bytes)
    "vosk-model-en.tar.gz": ("422faee21c5854959c6ab4ff94ee2774cb3500bfb6d5325edafe39ec32b12247", 41183558),
    "ort-wasm-simd-threaded.jsep.wasm": ("c46655e8a94afc45338d4cb2b840475f88e5012d524509916e505079c00bfa39", 21596019),
    "ort-wasm-simd-threaded.jsep.mjs": ("08fb86ec433c78bfb032c5d84a68b8e8e5a8d81268fa39e24314179a5767a5b9", 44484),
    "voice-ml.mjs": ("d9d438e815bca79358e19b4abd1846e2638a76d8d6de6a9d908d9e65c50926e9", 2216856),
}
TIMEOUT_S = 120
_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


def _version() -> str:
    from .update import installed_version      # babyseat, or membrane-seat before 0.7 (#303)
    return installed_version() or "0.3.0"


def base_url() -> str:
    return (os.environ.get("MEMBRANE_ASSETS_URL")
            or f"https://github.com/chedim/membrane/releases/download/v{_version()}/").rstrip("/") + "/"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ensure(name: str, dest: Path) -> Path | None:
    """The asset at dest/name -- fetched and verified if it isn't there yet;
    None when it isn't a known asset or the fetch failed (the page falls back:
    no wake word / the speech server)."""
    if name not in MANIFEST:
        return None
    want, size = MANIFEST[name]
    p = Path(dest) / name
    if p.is_file() and p.stat().st_size == size:
        return p
    with _guard:
        lock = _locks.setdefault(name, threading.Lock())
    with lock:                                   # one fetch per file, the others wait for it
        if p.is_file() and p.stat().st_size == size:
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        try:
            with urllib.request.urlopen(base_url() + name, timeout=TIMEOUT_S) as r, tmp.open("wb") as f:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    f.write(chunk)
            if _sha(tmp) != want:
                tmp.unlink(missing_ok=True)
                return None
            os.replace(tmp, p)
            return p
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None
