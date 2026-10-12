#!/usr/bin/env python3
"""STANDALONE MODE'S UI (2026-10-12): the package's own copy of the web UI,
made from the boat's HA integration www/ -- the HA paths rewritten to
membrane's own routes, HA-only files left behind. The copy is what ships;
the integration is never imported by the package.

    python scripts/sync_ui.py          # writes membrane/ui/*.js from the integration's www/

The big models (Vosk, ONNX wasm) are not copied: the seat serves them from its
data dir, downloaded on first use (ui_serve.py).
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "integrations" / "homeassistant" / "custom_components" / "ai_terminal" / "www"
DST = ROOT / "membrane" / "ui"
FILES = ("panel.js", "home.js", "field.js", "field3d.js", "three.min.js", "vosk.js")
LEAVE = ("stranger.js",)                       # HA's login page: no HA here
# fetched, not committed (git-ignored in the integration too): ui_serve finds them in
# <data dir>/assets/ or a checkout's www/
BIG = ("vosk-model-en.tar.gz", "ort-wasm-simd-threaded.jsep.wasm", "ort-wasm-simd-threaded.jsep.mjs", "voice-ml.mjs")


def transform(name: str, text: str) -> str:
    if not name.endswith((".js", ".mjs")) or name in ("three.min.js", "vosk.js"):
        return text
    text = text.replace("/api/ai_terminal/www/", "/ui/www/")
    text = text.replace("/api/ai_terminal/serve", "/ui/serve")
    text = text.replace("/api/ai_terminal/stt", "/ui/stt")
    text = text.replace("/api/ai_terminal/conf", "/ui/conf")
    text = re.sub(r"/api/ai_terminal/", "/ui/", text)
    if name == "home.js":                      # no HA menu button standalone
        old = 'this._menu = $(".hamenu"); this._haBtn = $(".ha");'
        if old not in text:
            raise SystemExit("home.js: the HA button hook moved; update sync_ui.py")
        text = text.replace(old, old + ' if (window.MEMBRANE_STANDALONE) this._haBtn.style.display = "none";')
    if name == "panel.js":                     # the speaker line: '[user: X]' standalone, '[HA user: X]' from HA
        old = 'const SPEAKER_PREFIX = "[HA user: ";'
        if old not in text:
            raise SystemExit("panel.js: SPEAKER_PREFIX moved; update sync_ui.py")
        text = text.replace(old, old + '\nconst SPEAKER_RE = /\\[(?:HA )?user: [^\\]\\n]{1,80}\\]/;')
        text = text.replace("t.startsWith(SPEAKER_PREFIX)", "/^\\s*\\[(?:HA )?user: /.test(t)")
        text = re.sub(r"(\b[\w.]+|String\(text \|\| \"\"\))\.includes\(SPEAKER_PREFIX\)",
                      lambda m: "SPEAKER_RE.test(" + m.group(1) + ")", text)
        if ".includes(SPEAKER_PREFIX)" in text or ".startsWith(SPEAKER_PREFIX)" in text:
            raise SystemExit("panel.js: a SPEAKER_PREFIX use the sync does not know")
    return text


def main() -> int:
    if not SRC.is_dir():
        print(f"no source UI at {SRC}", file=sys.stderr)
        return 1
    DST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        src = SRC / name
        if not src.exists():
            print(f"skip (missing): {name}", file=sys.stderr)
            continue
        if name.endswith((".js", ".mjs")):
            (DST / name).write_text(transform(name, src.read_text()))
        else:
            shutil.copy2(src, DST / name)
        print(f"synced {name}")
    left = [n for n in ("/api/ai_terminal",) for f in FILES if (DST / f).exists()
            and f not in ("three.min.js", "vosk.js") and n in (DST / f).read_text()]
    if left:
        print("WARNING: HA paths left in the copy", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
