"""THE WAKE LEDGER (2026-10-11, outcome learning's phase 1 -- the operator:
"can the net be represented by the field? ... can it grow on the field
naturally?"): every watch-loop wake and what became of it, one line each,
in <data>/voice/wakes.jsonl. Pure logging: nothing reads it at run time.

It is the training set for the field's own react/ignore gate: the
features a wake arrives with (its kind, entity, class, the entity's last
verdicts and settled consequences, the event's numbers, the hour, how
long since the last wake on it, the head of its task text -- embedded at
training time, not here) and its label (the loop's verdict, or why it
never reached a loop: folded into the digest, held, requeued).

A re-queued or held event comes round again: it is logged once per
(event, outcome), so a walk's held events don't flood the file.
"""
from __future__ import annotations

import collections
import json
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_seen: collections.OrderedDict = collections.OrderedDict()   # (event key, outcome) logged once
_SEEN_MAX = 4000
_last_wake: dict[str, float] = {}                            # entity -> the last logged wake's ts
NUM_KEYS = ("distance", "prev", "count", "depth", "value", "delta")
HEAD_MAX = 200


def path_of(srv) -> Path | None:
    mp = getattr(srv, "_models_path", None)
    if not mp:
        return None
    return Path(mp).parent / "voice" / "wakes.jsonl"


def _ekey(ev: dict) -> str:
    return str(ev.get("event_id") or f"{ev.get('kind')}|{ev.get('entity')}|{ev.get('ts')}")


def features(srv, ev: dict, now: float) -> dict:
    ent = str(ev.get("entity") or "")
    f = {"kind": str(ev.get("kind") or ""), "entity": ent, "hour": time.localtime(now).tm_hour,
         "readonly": bool(ev.get("readonly")), "since_last_s": None, "cls": None,
         "last_verdicts": [], "consequences": 0, "nums": {}}
    for k in NUM_KEYS:
        v = ev.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            f["nums"][k] = round(float(v), 4)
    if isinstance(ev.get("group"), dict):
        for k, v in ev["group"].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                f["nums"]["group_" + k] = v
    if ent in _last_wake:
        f["since_last_s"] = round(now - _last_wake[ent], 1)
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(ent) if (est is not None and ent) else None
    if doc is not None:
        pp = doc.properties or {}
        f["cls"] = getattr(doc, "cls", None)
        f["last_verdicts"] = [str(x.get("verdict") or "") for x in (pp.get("watch_decisions") or [])][-3:]
        f["consequences"] = len(pp.get("consequences") or [])
    try:
        from .watch_agents import loop_task_text
        f["head"] = loop_task_text(ev).split("\n", 1)[0][:HEAD_MAX]
    except Exception:
        f["head"] = ""
    return f


def log(srv, ev: dict, outcome: str, now: float | None = None, feats: dict | None = None,
        **extra) -> dict | None:
    """One wake's line: its features (taken as it arrived) + what became of it."""
    now = now or time.time()
    key = (_ekey(ev), outcome)
    with _lock:
        if key in _seen:
            return None
        _seen[key] = True
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)
    try:
        rec = {"ts": round(now, 3), "event_id": _ekey(ev), "outcome": outcome,
               **(feats if feats is not None else features(srv, ev, now)),
               **{k: v for k, v in extra.items() if v is not None}}
    except Exception:
        return None
    if outcome not in ("held", "requeue") and rec.get("entity"):
        _last_wake[rec["entity"]] = now
    p = path_of(srv)
    if p is not None:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with _lock, open(p, "a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass
    return rec
