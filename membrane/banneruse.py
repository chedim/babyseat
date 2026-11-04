"""#934: which banner lines a turn USES, per rule -- measure only, nothing is dropped.

The banner shows lines from two sources: the graph-walk rules (graphbanner, one BannerRule
entity each) and the recall rows (one entity, RECALL_EID). When a turn's banner is built the
lines shown are held per chat (srv._banner_shown); when the turn's reply lands (settle) each is
counted SHOWN, and USED when the reply carries what the line is about: a word of the line that
its template did not supply (graphbanner's "used" test -- the walk's lines were used in 70% of
turns), for a recall line a word only that line carries among the lines shown.

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
_lock = threading.Lock()


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


def shown_walk(srv, sid: str, pairs) -> None:
    """The graph-walk lines a banner carried: [(rule_eid, template, line)]. A rebuilt banner
    replaces the held walk lines of the same turn."""
    with _lock:
        held = srv.__dict__.setdefault("_banner_shown", {}).setdefault(sid, [])
        held[:] = [h for h in held if h["rule"] == RECALL_EID]
    for rule_eid, template, line in pairs:
        _hold(srv, sid, rule_eid, line, template)


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
        from .graphbanner import CLASS_RULE
        ent = QuantumEntity(eid=eid, cls=CLASS_RULE, properties={
            "name": "recall", "retired": True, "silent_birth": True,
            "summary": "the recall rows of the banner (not a walk rule): use counts only"})
        est.docs[eid] = ent
    return ent


def _add(srv, eid: str, shown: int, used: int, ts: float) -> None:
    ent = _entity(srv, eid)
    if ent is None:
        return
    pp = dict(ent.properties or {})
    hours = {k: dict(v) for k, v in (pp.get("use_hours") or {}).items()}
    hb = hours.setdefault(_hour(ts), {})
    hb["shown"] = int(hb.get("shown", 0)) + shown
    hb["used"] = int(hb.get("used", 0)) + used
    try:
        from .score import HOURS_KEEP
    except Exception:
        HOURS_KEEP = 48
    cut = float(_hour(ts)) - (HOURS_KEEP - 1) * 3600
    pp["use_hours"] = {h: b for h, b in hours.items() if float(h) >= cut}
    ent.properties = pp
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass


def _content(held: dict, others: list[dict]) -> set[str]:
    """The words a line is about: for a walk line, those its template did not supply; for a
    recall line, those no other recall line shown carries."""
    mine = _words(held["line"])
    if held["rule"] == RECALL_EID:
        for o in others:
            if o is not held:
                mine -= _words(o["line"])
        return mine
    return mine - _words(re.sub(r"\{\w+\.\w+\}", " ", held.get("template") or ""))


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
