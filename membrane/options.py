"""THE SEAT-WIDE OPTIONS (#344): what the viewer's options window (⚙) reads and
writes, through one endpoint (server.py):

    GET  /membrane/options   the options, their defaults, what is read-only
    POST /membrane/options   {key: value, ...} -- the writable ones only; a bad
                             value is a 400 with the reason, and nothing is
                             written (never a silent clamp)

They live in the seat's settings.json (settings.py), so a restart keeps them.
Per-device state (the last open chat tab, the camera) stays in the browser.

  self_cpu_share   Blurp's CPU share (#343): 0 < share <= 1, default 0.5. The
                   self loop and the record cap read it with cpu_share(srv)
                   -- the one reader: the file, validated on read -- so a
                   write applies without a restart.
  current_tau_s    the field current's fade (efield.CURRENT_TAU_S, seconds):
                   READ-ONLY here. It is a threshold; changing it needs the
                   operator's approval (a code change, not an option).
  energy           Blurp's energy, 0..1, and whether it is asleep (#345, #363):
                   READ-ONLY, from energy.report() for the seat's self; None
                   with no seat or no self yet. Energy replaced health.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import settings as S

CPU_SHARE_KEY = "self_cpu_share"
CPU_SHARE_DEFAULT = 0.5
WRITABLE = (CPU_SHARE_KEY,)
READ_ONLY = {
    "current_tau_s": "current_tau_s is read-only: changing the current's fade needs the operator's approval",
    "energy": "energy is read-only: it is Blurp's to report (#345), not a setting",
}
TAU_NOTE = "changing it needs the operator's approval"


def _share_ok(v) -> bool:
    return (isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and 0 < v <= 1)


def _settings_path(srv=None) -> Path:
    """The seat's settings.json: beside the seat's self/ dir when a seat is
    given (its data dir, where the models file sits), else settings.path()
    (MEMBRANE_DATA_DIR) -- the same file on a running seat."""
    mp = getattr(srv, "_models_path", None) if srv is not None else None
    return Path(mp).parent / S.FILE if mp else S.path()


def cpu_share(srv=None) -> float:
    """Blurp's CPU share as the seat has it -- THE one reader (#343's loop,
    self_loop.cpu_share, and #345's record cap delegate here): the settings
    file's value when it is valid, else the default. Read on every call, so a
    write applies without a restart."""
    try:
        raw = json.loads(_settings_path(srv).read_text())
    except (OSError, ValueError):
        raw = {}
    v = raw.get(CPU_SHARE_KEY) if isinstance(raw, dict) else None
    return float(v) if _share_ok(v) else CPU_SHARE_DEFAULT


def _energy(srv):
    """Blurp's energy and asleep (#363), or None: no seat, or no self yet."""
    if srv is None:
        return None
    from . import energy as E
    try:
        r = E.report(srv)
        return {"energy": float(r["energy"]), "asleep": bool(r["asleep"])}
    except Exception:
        return None


def read(srv=None) -> dict:
    from .efield import CURRENT_TAU_S
    return {CPU_SHARE_KEY: cpu_share(),
            "defaults": {CPU_SHARE_KEY: CPU_SHARE_DEFAULT},
            "writable": list(WRITABLE),
            "current_tau_s": float(CURRENT_TAU_S), "current_tau_note": TAU_NOTE,
            "energy": _energy(srv)}


def apply(body) -> dict:
    """Validate every key first, then write them together; ValueError (the
    reason) on anything else, with nothing written. -> read()."""
    if not isinstance(body, dict) or not body:
        raise ValueError("expected a JSON object of options, e.g. {\"self_cpu_share\": 0.5}")
    changes = {}
    for k, v in body.items():
        if k in READ_ONLY:
            raise ValueError(READ_ONLY[k])
        if k not in WRITABLE:
            raise ValueError(f"unknown option: {k} (writable: {', '.join(WRITABLE)})")
        if k == CPU_SHARE_KEY:
            if not _share_ok(v):
                raise ValueError(f"self_cpu_share must be a number in (0, 1], got {v!r}")
            changes[k] = float(v)
    S.save(changes)
    return read()
