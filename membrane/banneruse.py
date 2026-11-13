"""#934: which banner lines a turn USES, per rule -- measure only, nothing is dropped.

The banner's recall rows are one entity (RECALL_EID). When a turn's banner is built the lines shown
are held per chat (srv._banner_shown); when the turn's reply lands (settle) each is counted SHOWN, and
USED when the reply carries a word only that line carries among the lines shown.

Counts sit on the rule entity in hourly buckets (properties.use_hours: hour -> {shown, used},
like score's), so they survive a restart; the use rate is used/shown, read where rules are listed.
There are no cutoffs here: the numbers are for the operator to read.
"""
from __future__ import annotations

import re
import threading
import time

from .entities import MEMBRANE_NS, QuantumEntity

RECALL_EID = MEMBRANE_NS + "bannerrule:recall"
_WORD = re.compile(r"[a-z0-9][a-z0-9_'-]*")
_PREFIX = re.compile(r"^\W*#\d+\s*\([^)]*\)\s*")
_lock = threading.RLock()


def _body(line: str) -> str:
    """A recall row without its '#N (age, label)' prefix: that is the banner's own wording."""
    return _PREFIX.sub("", line or "", count=1)


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2 and not w.isdigit()}


def _hour(ts: float) -> str:
    return str(int(ts // 3600) * 3600)


def _hold(srv, sid: str, rule_eid: str, line: str, template: str = "") -> None:
    if not sid or not line:
        return
    with _lock:
        held = srv.__dict__.setdefault("_banner_shown", {}).setdefault(sid, [])
        held.append({"rule": rule_eid, "line": line, "template": template})


def shown_recall(srv, sid: str, lines) -> None:
    """The recall banner's lines for the turn."""
    with _lock:
        held = srv.__dict__.setdefault("_banner_shown", {}).setdefault(sid, [])
        held[:] = [h for h in held if h["rule"] != RECALL_EID]
    for ln in lines or []:
        _hold(srv, sid, RECALL_EID, str(ln))


def _entity(srv, eid: str):
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(eid)
    if ent is None and eid == RECALL_EID:
        ent = QuantumEntity(eid=eid, cls="BannerRule", properties={
            "name": "recall", "kind": "recall", "silent_birth": True,
            "summary": "the recall rows of the banner use counts only"})
        est.docs[eid] = ent
    return ent


def _add(srv, eid: str, shown: int, used: int, ts: float) -> None:
    ent = _entity(srv, eid)
    if ent is None:
        return
    try:
        from .score import HOURS_KEEP
    except Exception:
        HOURS_KEEP = 48
    with _lock:                      # the whole read-modify-write: two settles must not lose a count
        pp = dict(ent.properties or {})
        hours = {k: dict(v) for k, v in (pp.get("use_hours") or {}).items()}
        hb = hours.setdefault(_hour(ts), {})
        hb["shown"] = int(hb.get("shown", 0)) + shown
        hb["used"] = int(hb.get("used", 0)) + used
        cut = float(_hour(ts)) - (HOURS_KEEP - 1) * 3600
        pp["use_hours"] = {h: b for h, b in hours.items() if float(h) >= cut}
        ent.properties = pp
        try:
            srv.entity_store.upsert(ent, embedder=None)
        except Exception:
            pass


def _content(held: dict, others: list[dict]) -> set[str]:
    """The words a recall line is about: those no other recall line shown carries."""
    mine = _words(_body(held["line"]))
    for o in others:
        if o is not held:
            mine -= _words(_body(o["line"]))
    return mine


def settle(srv, sid: str, reply: str, ts: float | None = None) -> dict:
    """The turn's reply landed: count what the banner showed it, once."""
    with _lock:
        held = (srv.__dict__.get("_banner_shown") or {}).pop(sid, None) or []
    if not held:
        return {}
    ts = ts or time.time()
    said = _words(reply)
    per: dict[str, list[int]] = {}
    for h in held:
        c = per.setdefault(h["rule"], [0, 0])
        c[0] += 1
        if _content(h, held) & said:
            c[1] += 1
    for eid, (s, u) in per.items():
        _add(srv, eid, s, u, ts)
    return per


def counts(ent) -> dict:
    """{shown, used, rate} summed over the entity's kept hours."""
    hours = (getattr(ent, "properties", None) or {}).get("use_hours") or {}
    s = sum(int(b.get("shown", 0)) for b in hours.values())
    u = sum(int(b.get("used", 0)) for b in hours.values())
    return {"shown": s, "used": u, "rate": (u / s) if s else None}
