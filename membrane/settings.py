"""THE SEAT'S SETTINGS (standalone mode, 2026-10-12): one JSON file beside the
field, `<data dir>/settings.json` -- what a person picks in the UI's first run
(the seat's name, the model backend, voice), read at boot; CLI flags still win.

The data dir is the one the CLI binds (./.membrane, or the field's parent);
MEMBRANE_DATA_DIR names it for the parts that have no args (the UI routes).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

FILE = "settings.json"
DEFAULTS = {
    "name": "",               # the seat's name (the name ceremony); "" = not picked yet
    "self_name": "",          # self's name on this seat (#283, self_name.py); "" = the default, Blurp
    "aliases": [],            # what the STT hears for it
    "backend": "",            # the Ollama / OpenAI-compatible URL ("" = the CLI's)
    "chat_model": "",
    "embed_model": "",
    "stt": "browser",         # "browser" (Vosk / voice-ml in the page) | a whisper-server URL
    "voice_out": False,       # the default for a new person's voice:out
    "language": "en",
    "ui_lan_open": True,      # the UI without a password from private / loopback / tailnet addresses
    "self_cpu_share": 0.5,    # #343/#344: Blurp's CPU share, 0 < share <= 1 (options.py validates, the ⚙ window sets it)
}
_lock = threading.Lock()


def data_dir() -> Path:
    return Path(os.environ.get("MEMBRANE_DATA_DIR") or ".membrane")


def path() -> Path:
    return data_dir() / FILE


def exists() -> bool:
    return path().exists()


def load() -> dict:
    out = dict(DEFAULTS)
    try:
        raw = json.loads(path().read_text())
        if isinstance(raw, dict):
            out.update({k: v for k, v in raw.items() if not k.startswith("_")})
    except (OSError, ValueError):
        pass
    return out


def save(changes: dict) -> dict:
    """Merge `changes` (known keys only) into the file; returns the whole set."""
    with _lock:
        cur = load()
        for k, v in (changes or {}).items():
            if k in DEFAULTS:
                cur[k] = v
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cur, indent=2, sort_keys=True))
        os.replace(tmp, p)
        return cur

