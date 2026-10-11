"""THE SCORE (2026-10-11, the operator: "state in the doctrine that the LLM's
goal is to build the field to substitute the LLM for mundane things,
lowering the LLM's participation ... without sacrificing quality. Feel free
to reward it ... make the score a field entity, not the banner").

membrane::score is an entity like any other: Argus reads it when he wants
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
COUNT_V = 2                                      # v2: a tick counts only when its output changed

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


def note_changed(kind: str, eid: str, value) -> bool:
    """Count only when this entity's output differs from the last counted one
    (a tick re-rendering the same thing every beat is busywork, not work)."""
    import json as _json
    try:
        sig = _json.dumps(value, sort_keys=True, default=str)
    except Exception:
        sig = repr(value)
    with _lock:
        if _last.get(eid) == sig:
            return False
        _last[eid] = sig
    note(kind)
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
    cur["ts"] = round(now, 1)
    pp["hours"] = hours
    pp["now"] = cur
    if new_credits:
        pp["credits"] = (list(pp.get("credits") or []) + new_credits)[-CREDITS_KEEP:]
    t = cur.get("trend")
    pp["state"] = (f"24h: the field did {cur['field_total']} things itself, you took {cur['rounds_total']} "
                   f"rounds -- {round(cur['share'] * 100)}% field; score {round(cur['score'] * 100)}"
                   + (f" ({'+' if t >= 0 else ''}{round(t * 100)} vs the day before)" if t is not None else "")
                   + f"; {cur['errors']} script errors, {cur['failing_scripts']} scripts failing")
    ent.properties = pp
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass
    return cur


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
