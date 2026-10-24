"""The seat's BACKGROUND token tally (#200).

Foreground turns carry their usage in the serve-msgs store; every model call
the seat makes OUTSIDE a serve turn -- the thinker's deliberation, the
proxy-only watch degrade, the claim judge, the router's boot interview,
/delegate -- lands here instead, per lane and model:

  <data dir>/bg-usage.json
  {"since": ts, "updated": ts,
   "lanes": {lane: {model: {"calls", "input", "output", "reasoning"}}},
   "slots": {slot start: {lane: {model: {...the same}}}}}

`lanes` is the all-time tally; `slots` is the same split per 10-minute slot
(SLOT_S), kept about a day (KEEP_S), so the 📊 stats window can say today's
and the last hour's background calls without a log (tokenomics.py).

The same ruler as the foreground (`server._tokens_fill`): input = prompt
tokens, output = completion tokens, reasoning = the provider's reasoning
detail when it gives one (Ollama's /api/chat does not: its eval_count is the
whole generation, thinking included).

Write-through, read-modify-write under a lock: background calls are a few a
minute at most (the thinker's cap is 4/min), so the file is the tally and a
restart loses nothing. The data dir is MEMBRANE_DATA_DIR (the seat's boot
sets it); with none set -- tests, one-off CLI tools -- the tally stays in
memory and nothing lands on disk. Never raises: accounting must not break
the call it accounts for.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

FILE = "bg-usage.json"
SLOT_S = 600                 # one slot: 10 minutes
KEEP_S = 26 * 3600           # slots kept: since local midnight, plus a DST hour
_KEYS = ("calls", "input", "output", "reasoning")
_lock = threading.Lock()
_mem: dict = {}


def path() -> Path | None:
    d = os.environ.get("MEMBRANE_DATA_DIR")
    return Path(d) / FILE if d else None


def _empty() -> dict:
    now = time.time()
    return {"since": now, "updated": now, "lanes": {}}


def load(p) -> dict | None:
    """The persisted tally at `p`, or None when there is none (or it is not
    a tally)."""
    try:
        doc = json.loads(Path(p).read_text())
    except Exception:
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("lanes"), dict) \
        else None


def _i(v) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0


def record(lane: str, model: str, input: int = 0, output: int = 0,
           reasoning: int = 0) -> None:
    """One background call's usage onto the tally."""
    global _mem
    try:
        with _lock:
            p = path()
            doc = (load(p) if p else _mem) or _empty()
            now = time.time()
            slots = doc.get("slots") if isinstance(doc.get("slots"), dict) else {}
            for k in [k for k in slots if _i(k) <= now - KEEP_S]:
                del slots[k]
            doc["slots"] = slots
            slot = slots.setdefault(str(int(now // SLOT_S * SLOT_S)), {})
            for tally in (doc["lanes"], slot):
                row = tally.setdefault(lane or "background", {}) \
                    .setdefault(model or "?", dict.fromkeys(_KEYS, 0))
                for k, v in (("calls", 1), ("input", input), ("output", output),
                             ("reasoning", reasoning)):
                    row[k] = _i(row.get(k)) + _i(v)
            doc["updated"] = now
            if p is None:
                _mem = doc
                return
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text(json.dumps(doc))
            os.replace(tmp, p)
    except Exception:
        pass


def from_ollama(lane: str, model: str, resp) -> None:
    """Tally an Ollama /api/chat response (prompt_eval_count / eval_count)."""
    if isinstance(resp, dict):
        record(lane, model, input=_i(resp.get("prompt_eval_count")),
               output=_i(resp.get("eval_count")))


def from_openai(lane: str, model: str, resp) -> None:
    """Tally an OpenAI-compatible response's `usage`."""
    u = resp.get("usage") if isinstance(resp, dict) else None
    if not isinstance(u, dict):
        u = {}
    det = u.get("completion_tokens_details")
    record(lane, model, input=_i(u.get("prompt_tokens")),
           output=_i(u.get("completion_tokens")),
           reasoning=_i(det.get("reasoning_tokens"))
           if isinstance(det, dict) else 0)


def window(doc: dict, since: float) -> dict:
    """The slots that end after `since`, summed: {lane: {model: row}}. A
    slot is 10 minutes, so a window reads up to one slot more than asked."""
    out: dict = {}
    for k, lanes in ((doc or {}).get("slots") or {}).items():
        if _i(k) + SLOT_S <= since or not isinstance(lanes, dict):
            continue
        for lane, models in lanes.items():
            for model, row in (models or {}).items():
                acc = out.setdefault(lane, {}).setdefault(model, dict.fromkeys(_KEYS, 0))
                for key in _KEYS:
                    acc[key] += _i((row or {}).get(key))
    return out


def totals(doc: dict) -> dict:
    """The tally summed across lanes and models."""
    out = dict.fromkeys(_KEYS, 0)
    for models in (doc.get("lanes") or {}).values():
        for row in (models or {}).values():
            for k in _KEYS:
                out[k] += _i((row or {}).get(k))
    return out
