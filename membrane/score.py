"""THE SCORE (2026-10-11, the operator: "state in the doctrine that the LLM's
goal is to build the field to substitute the LLM for mundane things,
lowering the LLM's participation ... without sacrificing quality. Feel free
to reward it ... make the score a field entity, not the banner").

membrane::score is an entity like any other: the seat reads it when he wants
to know how he is doing; nothing injects it into a turn. It counts, in
hourly buckets kept on the entity (they survive a restart):

  field work   what the field did WITHOUT a model round:
                 reading -- a getter's reading that changed, not asked for
                            by a model's read (the thinker's, a watch's)
                 tick    -- a tick script that moved its entity
                 answer  -- a turn the field answered itself (an intercept,
                            field-only)
  rounds       the model's rounds, per lane
  errors       a script that failed (quality: the field's work must hold)
  credits      the first good reading of a new getter/tick -- a thing the
               field now does for him (the ring, newest last)

share = field / (field + rounds); score = share x (1 - error rate), both
over the last 24 h, with the 24 h before for the trend. Busywork earns
nothing: an unchanged reading, a tick that moved nothing, a forced read
are not counted.
"""
from __future__ import annotations

import threading
import time

ENT = "score"
REFRESH_S = 60.0
HOURS_KEEP = 48
CREDITS_KEEP = 20
FIELD_KINDS = ("reading", "tick", "answer")
COUNT_V = 3                                      # v3: entity-hours (once per entity per hour per kind)
DAYS_KEEP = 14
GROWTH_FLOOR = 0.5                               # a collapsing field can halve the score, no more

_lock = threading.Lock()
_pending: dict[str, dict[str, int]] = {}         # hour -> {kind: n}, not yet on the entity
_credits: list[dict] = []
_seat = None


def bind(srv) -> None:
    global _seat
    _seat = srv


def _hour(ts: float) -> str:
    return str(int(ts // 3600) * 3600)


def note(kind: str, n: int = 1, lane: str = "", ts: float | None = None) -> None:
    """Count one thing (cheap: in memory; the refresh puts it on the entity)."""
    key = f"round:{lane or 'chat'}" if kind == "round" else kind
    h = _hour(ts or time.time())
    with _lock:
        b = _pending.setdefault(h, {})
        b[key] = b.get(key, 0) + int(n)


_last: dict[str, str] = {}                      # eid -> the last output a tick counted for
_counted: dict[tuple, bool] = {}                # (kind, eid, hour) already counted


def note_changed(kind: str, eid: str, value=None, ts: float | None = None) -> bool:
    """ENTITY-HOURS: an entity kept live counts ONCE per hour per kind, and
    only when its output differs from the last counted one (2026-10-11: 214
    ticks in five minutes from a few entities re-rendering every beat -- a
    busy clock must not outweigh the work)."""
    import json as _json
    try:
        sig = _json.dumps(value, sort_keys=True, default=str)
    except Exception:
        sig = repr(value)
    h = _hour(ts or time.time())
    with _lock:
        if value is not None and _last.get(eid) == sig:
            return False
        _last[eid] = sig
        if _counted.get((kind, eid, h)):
            return False
        _counted[(kind, eid, h)] = True
        if len(_counted) > 50000:
            for k in [k for k in _counted if k[2] != h]:
                _counted.pop(k, None)
    note(kind, ts=ts)
    return True


def credit(what: str, eid: str = "", ts: float | None = None) -> None:
    with _lock:
        _credits.append({"ts": round(ts or time.time(), 1), "what": str(what)[:160], "entity": eid})


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties={
            "name": "score",
            "summary": ("YOUR SCORE: how much of the mundane work the field does without you "
                        "(getters, ticks, its own answers) against your model rounds, held by "
                        "quality (failing scripts). Higher is better; read `now` and `credits`.")})
        est.docs[ent.eid] = ent
    return ent


def _sum(hours: dict, lo: float, hi: float) -> dict:
    out: dict[str, int] = {}
    for h, b in hours.items():
        if lo <= float(h) < hi:
            for k, v in b.items():
                out[k] = out.get(k, 0) + int(v)
    return out


def summarize(tot: dict) -> dict:
    field = {k: int(tot.get(k, 0)) for k in FIELD_KINDS}
    rounds = {k.split(":", 1)[1]: int(v) for k, v in tot.items() if k.startswith("round:")}
    f, r, e = sum(field.values()), sum(rounds.values()), int(tot.get("error", 0))
    share = f / (f + r) if (f + r) else 0.0
    err_rate = e / (f + e) if (f + e) else 0.0
    return {"field": field, "field_total": f, "rounds": rounds, "rounds_total": r, "errors": e,
            "share": round(share, 3), "score": round(share * (1.0 - err_rate), 3)}


def refresh(srv=None, now: float | None = None) -> dict:
    """The pending counts onto the entity; `now` = the last 24 h and the trend."""
    srv = srv or _seat
    ent = _entity(srv) if srv is not None else None
    if ent is None:
        return {}
    now = now or time.time()
    with _lock:
        pend = {h: dict(b) for h, b in _pending.items()}
        _pending.clear()
        new_credits = list(_credits)
        _credits.clear()
    pp = ent.properties or {}
    if pp.get("v") != COUNT_V:                  # the counting changed: the old hours meant something else
        pp["hours"], pp["v"] = {}, COUNT_V
    hours = {k: dict(v) for k, v in (pp.get("hours") or {}).items()}
    for h, b in pend.items():
        hb = hours.setdefault(h, {})
        for k, v in b.items():
            hb[k] = hb.get(k, 0) + v
    cut = float(_hour(now)) - (HOURS_KEEP - 1) * 3600
    hours = {h: b for h, b in hours.items() if float(h) >= cut}
    cur = summarize(_sum(hours, now - 86400, now + 3600))
    prev = summarize(_sum(hours, now - 2 * 86400, now - 86400))
    cur["trend"] = round(cur["score"] - prev["score"], 3) if prev["field_total"] + prev["rounds_total"] else None
    cur["failing_scripts"] = _failing(srv)
    g = growth(srv)
    days = dict(pp.get("days") or {})
    day = str(int(now // 86400) * 86400)
    if g:
        days[day] = g                               # today's latest picture
    days = dict(sorted(days.items())[-DAYS_KEEP:])
    pp["days"] = days
    before = [v for k, v in sorted(days.items()) if k < day]
    g_prev = before[-1] if before else None
    cur["growth"] = g
    cur["growth_factor"] = growth_factor(g, g_prev)
    if g_prev:
        cur["growth_trend"] = {k: (round(g[k] - g_prev[k], 3) if isinstance(g.get(k), (int, float))
                                   and isinstance(g_prev.get(k), (int, float)) else None)
                               for k in ("eff_rank", "rank_ratio", "free_energy", "entities", "scripts", "methods")}
    cur["score"] = round(cur["score"] * cur["growth_factor"], 3)
    cur["ts"] = round(now, 1)
    pp["hours"] = hours
    pp["now"] = cur
    if new_credits:
        pp["credits"] = (list(pp.get("credits") or []) + new_credits)[-CREDITS_KEEP:]
    t = cur.get("trend")
    pp["state"] = (f"24h: the field did {cur['field_total']} things itself, you took {cur['rounds_total']} "
                   f"rounds -- {round(cur['share'] * 100)}% field; score {round(cur['score'] * 100)}"
                   + (f" ({'+' if t >= 0 else ''}{round(t * 100)} vs the day before)" if t is not None else "")
                   + f"; {cur['errors']} script errors, {cur['failing_scripts']} scripts failing"
                   + (f"; the field: eff rank {g.get('eff_rank')}, {g.get('entities')} entities, "
                      f"{g.get('scripts')} scripts, {g.get('methods')} methods" if g else "")
                   + (f" -- DEGRADING (x{cur['growth_factor']})" if cur["growth_factor"] < 1 else ""))
    ent.properties = pp
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass
    return cur


def growth(srv) -> dict:
    """The field's own size and variety (the operator: "membrane should
    grow, not degrade"): effective rank of the live entity states (their
    geometry), its ratio, free energy and the contradicted share; the
    library's size -- entities, scripts, methods."""
    out: dict = {}
    fld = getattr(srv, "efield", None)
    try:
        if fld is not None:
            geo = fld.geometry()
            out.update({k: geo.get(k) for k in ("eff_rank", "rank_ratio", "free_energy", "conflict_frac")})
    except Exception:
        pass
    try:
        docs = list((srv.entity_store.docs or {}).values())
        out["entities"] = len(docs)
        out["scripts"] = sum(1 for d in docs if any(str(k).endswith("_script") and v
                                                    for k, v in (d.properties or {}).items()))
        out["methods"] = sum(1 for d in docs if (d.properties or {}).get("method_name"))
    except Exception:
        pass
    return out


def growth_factor(g: dict | None, g_prev: dict | None) -> float:
    """Growth never adds to the score; a drop in effective rank against the
    day before takes away (proportionally, floored at GROWTH_FLOOR)."""
    try:
        a, b = float(g["eff_rank"]), float(g_prev["eff_rank"])
    except (TypeError, KeyError, ValueError):
        return 1.0
    if b <= 0 or a >= b:
        return 1.0
    return round(max(GROWTH_FLOOR, a / b), 3)


def _failing(srv) -> int:
    try:
        return sum(1 for d in list((srv.entity_store.docs or {}).values())
                   if (d.properties or {}).get("script_failures") or (d.properties or {}).get("script_disabled"))
    except Exception:
        return 0


def start(srv) -> None:
    bind(srv)

    def loop():
        while True:
            time.sleep(REFRESH_S)
            try:
                refresh(srv)
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="score").start()
