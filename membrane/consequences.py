"""CONSEQUENCES (2026-10-11, Argus on what an AGI would need: "I see tool
outputs, not outcomes ... long-horizon feedback is what turns a chain of
responses into judgment").

A decision that ACTED (a loop's DONE/STEP, a writing tool from chat or a
loop) is recorded with a small snapshot of its entity. At each horizon
(1 h, 24 h, 72 h) the settler looks at what followed -- the entity now vs
the snapshot, the field's activity on it from OTHER sessions, later
decisions on it, a goal's outcome -- and writes one factual line. No
model, no judgement of good or bad: the facts, back to the seat.

  on the entity   `consequences` (the last 3 lines); a loop waking on it
                  reads them beside its last passes
  in the chat     the diff's "+ settled:" lines (watermarked, like conflicts)
  in the field    an observation event (tool consequence) on its subject,
                  so recall and the night can find it
  the score       score.note("consequence")

membrane::consequences keeps the open records (they survive restarts).
"""
from __future__ import annotations

import threading
import time

ENT = "consequences"
HORIZONS = (3600.0, 86400.0, 3 * 86400.0)
SETTLE_S = 300.0
COALESCE_S = 600.0
PENDING_MAX = 300
SETTLED_MAX = 100
ON_ENTITY = 3
SNAP_KEYS = ("state", "value", "status", "online", "enabled", "fulfilled_ts", "blocked_until")
ACTED = ("done", "step")
WRITE_TOOLS = ("exec", "actuate", "entity", "edit", "learn", "forget", "memory", "intend", "goal",
               "watch", "script", "method", "fulfill")
READ_ACTIONS = ("read", "find", "list", "get", "search", "show", "look", "describe", "help")

_lock = threading.Lock()
_log: list[dict] = []                 # settled lines for the chat diff: {n, eid, line}
_N = {"n": 0}
_SEAT = {"srv": None}


def counter() -> int:
    return int(_N["n"])


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties={
            "name": "consequences",
            "summary": ("WHAT FOLLOWED YOUR DECISIONS: each decision that acted is looked at again "
                        "1 h, 24 h and 72 h later -- the entity then vs now, what else moved it, "
                        "whether it was decided again. Facts, not grades; `settled` holds the lines.")})
        est.docs[ent.eid] = ent
    return ent


def _doc(est, eid: str):
    docs = getattr(est, "docs", None) or {}
    d = docs.get(eid)
    if d is None and eid:
        for k in list(docs):
            if k.endswith("::" + eid) or k.endswith(":" + eid):
                return docs[k]
    return d


def _snap(doc) -> dict:
    pp = (doc.properties or {}) if doc is not None else {}
    out = {}
    for k in SNAP_KEYS:
        v = pp.get(k)
        if v is None or isinstance(v, (dict, list)):
            continue
        out[k] = v if not isinstance(v, str) else v[:80]
    return out


def is_write(tool: str, args: dict) -> bool:
    t = str(tool or "").lower()
    if not any(t == w or t.endswith("_" + w) or t.endswith("." + w) for w in WRITE_TOOLS):
        return False
    act = str((args or {}).get("action") or "").lower() if isinstance(args, dict) else ""
    return act not in READ_ACTIONS


def record(srv, eid: str, what: str, who: str = "", session: str = "", kind: str = "",
           now: float | None = None) -> dict | None:
    """One decision that acted, on `eid`."""
    srv = srv or _SEAT["srv"]
    est = getattr(srv, "entity_store", None)
    if est is None or not eid:
        return None
    now = now or time.time()
    doc = _doc(est, eid)
    real = doc.eid if doc is not None else eid
    ent = _entity(srv)
    if ent is None or real == ent.eid:
        return None
    with _lock:
        pp = ent.properties or {}
        pend = list(pp.get("pending") or [])
        for r in reversed(pend):          # coalesce: one per (session, entity) per window
            if (r.get("eid") == real and r.get("session") == session and session
                    and now - float(r.get("ts") or 0) < COALESCE_S):
                r["what"] = (str(r.get("what") or "") + "; " + str(what))[-160:]
                if kind in ACTED:
                    r["kind"] = kind
                ent.properties = pp
                return r
        rec = {"id": f"c{int(now * 1000)}", "eid": real, "ts": round(now, 1), "who": str(who or "?")[:40],
               "kind": kind, "what": str(what or "")[:160], "session": session or "",
               "snap": _snap(doc), "done": 0}
        pp["pending"] = (pend + [rec])[-PENDING_MAX:]
        pp["state"] = f"{len(pp['pending'])} decision(s) waiting to be looked at again"
        ent.properties = pp
    return rec


def _fmt_dt(h: float) -> str:
    return f"{int(h // 3600)}h" if h < 86400 else f"{int(h // 86400)}d"


def _activity(srv, rec: dict, hi: float) -> dict:
    """The field's deltas on the entity in (ts, hi], other sessions only."""
    fld = getattr(srv, "efield", None)
    out = {"streams": {}, "operator": 0}
    if fld is None:
        return out
    try:
        from .classes import short_name
        ids = {rec["eid"], short_name(rec["eid"])}
        rows = []
        for i in ids:
            rows += [d for d in (fld.deltas_of(i) or []) if rec["ts"] < float(d.get("ts") or 0) <= hi]
        evs = fld.b.get_multi("events", list({d.get("event") for d in rows if d.get("event")})) if rows else {}
        seen = set()
        for d in rows:
            k = d.get("event")
            if k in seen:
                continue
            seen.add(k)
            ev = evs.get(k) or {}
            sess = str(ev.get("session") or d.get("session") or "")
            if rec.get("session") and sess == rec["session"]:
                continue
            if ev.get("tool") == "consequence":
                continue
            s = str(ev.get("stream") or d.get("stream") or "?")
            if s == "discourse" and str(ev.get("role") or d.get("role") or "") == "user":
                out["operator"] += 1
                s = "operator"
            out["streams"][s] = out["streams"].get(s, 0) + 1
    except Exception:
        pass
    return out


def _later(srv, rec: dict, hi: float, pend: list) -> int:
    n = sum(1 for r in pend if r is not rec and r.get("eid") == rec["eid"] and rec["ts"] < float(r.get("ts") or 0) <= hi)
    doc = _doc(getattr(srv, "entity_store", None), rec["eid"])
    for x in ((doc.properties or {}).get("watch_decisions") or []) if doc is not None else []:
        if (rec["ts"] + 1 < float(x.get("ts") or 0) <= hi and x.get("session") != rec.get("session")
                and str(x.get("verdict")) in ACTED):
            if not any(abs(float(r.get("ts") or 0) - float(x.get("ts") or 0)) < 5 for r in pend):
                n += 1
    return n


def settle_line(srv, rec: dict, h: float, now: float, pend: list) -> str:
    est = getattr(srv, "entity_store", None)
    doc = _doc(est, rec["eid"])
    at = time.strftime("%m-%d %H:%M", time.localtime(rec["ts"]))
    from .classes import short_name
    head = f"{at} {rec.get('who') or 'you'} {str(rec.get('kind') or 'acted').upper()} \"{rec.get('what', '')[:100]}\" on {short_name(rec['eid'])}"
    bits = []
    if doc is None:
        bits.append("the entity is gone")
    else:
        now_s = _snap(doc)
        moved = [f"{k} {rec['snap'].get(k)!s}→{now_s.get(k)!s}" for k in sorted(set(rec["snap"]) | set(now_s))
                 if k not in ("fulfilled_ts", "blocked_until") and rec["snap"].get(k) != now_s.get(k)]
        if moved:
            bits.append("; ".join(moved[:3]))
        elif rec["snap"]:
            bits.append("its state held")
        pp = doc.properties or {}
        if pp.get("fulfilled_ts") and not rec["snap"].get("fulfilled_ts"):
            bits.append("the goal was fulfilled")
        elif str(pp.get("status") or "") == "blocked" or pp.get("blocked_until"):
            bits.append("the goal is parked")
    act = _activity(srv, rec, rec["ts"] + h)
    if act["streams"]:
        bits.append(", ".join(f"{n} {s}" for s, n in sorted(act["streams"].items(), key=lambda x: -x[1])[:4])
                    + " change(s) since")
    else:
        bits.append("nothing else touched it")
    if act["operator"]:
        bits.append(f"the operator spoke of it {act['operator']}×")
    again = _later(srv, rec, rec["ts"] + h, pend)
    bits.append(f"decided again {again}×" if again else "not decided again")
    return f"{head} → {_fmt_dt(h)}: " + "; ".join(bits)


def _publish(srv, rec: dict, line: str, now: float) -> None:
    est = getattr(srv, "entity_store", None)
    doc = _doc(est, rec["eid"])
    if doc is not None:
        pp = doc.properties or {}
        pp["consequences"] = (list(pp.get("consequences") or []) + [{"ts": round(now, 1), "line": line}])[-ON_ENTITY:]
        doc.properties = pp
        try:
            est.upsert(doc, embedder=None)
        except Exception:
            pass
    with _lock:
        _N["n"] += 1
        _log.append({"n": _N["n"], "eid": rec["eid"], "line": line})
        del _log[:-64]
    fld = getattr(srv, "efield", None)
    if fld is not None:
        try:
            from . import streams as ST
            from .classes import short_name
            subj = short_name(rec["eid"])
            fld.write_events([ST.Event(stream="observation", tool="consequence", args={"entity": subj},
                                       result_head=line, outcome="ok", ts=now, subject=subj)],
                             session="consequences")
        except Exception:
            pass
    try:
        from . import score as _SC
        _SC.note("consequence")
    except Exception:
        pass


def settle(srv=None, now: float | None = None) -> int:
    """Every record whose next horizon passed: its line. Returns how many."""
    srv = srv or _SEAT["srv"]
    ent = _entity(srv)
    if ent is None:
        return 0
    now = now or time.time()
    pp = ent.properties or {}
    pend = list(pp.get("pending") or [])
    out, keep = [], []
    for rec in pend:
        i = int(rec.get("done") or 0)
        # the latest horizon passed: one missed while the seat was down settles once, there
        j = max((x for x in range(len(HORIZONS)) if now >= float(rec["ts"]) + HORIZONS[x]), default=-1)
        if j >= i:
            try:
                line = settle_line(srv, rec, HORIZONS[j], now, pend)
            except Exception as e:      # noqa: BLE001
                line = f"(couldn't settle: {e!r})"
            out.append((rec, line))
            i = j + 1
        rec["done"] = i
        if i < len(HORIZONS):
            keep.append(rec)
    for rec, line in out:
        _publish(srv, rec, line, now)
    with _lock:
        pp["pending"] = keep
        pp["settled"] = (list(pp.get("settled") or []) + [{"ts": round(now, 1), "eid": r["eid"], "line": ln}
                                                          for r, ln in out])[-SETTLED_MAX:]
        pp["state"] = (f"{len(keep)} decision(s) waiting to be looked at again; "
                       f"{len(pp['settled'])} settled line(s) kept")
        ent.properties = pp
    try:
        getattr(srv, "entity_store").upsert(ent, embedder=None)
    except Exception:
        pass
    return len(out)


def diff_lines(mark: int, k: int = 2) -> list[str]:
    """The settled lines after the watermark (the chat diff)."""
    with _lock:
        new = [x for x in _log if x["n"] > int(mark or 0)]
    return [f"+ settled: {x['line'][:300]}" for x in new[-k:]]


def on_entity(doc) -> list[str]:
    return [str(x.get("line") or "") for x in ((doc.properties or {}).get("consequences") or [])][-ON_ENTITY:]


def bind(srv) -> None:
    _SEAT["srv"] = srv
    try:
        ent = _entity(srv)
        if ent is not None:
            srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass


def start(srv) -> None:
    bind(srv)

    def loop():
        while True:
            time.sleep(SETTLE_S)
            try:
                settle(srv)
            except Exception:
                pass

    threading.Thread(target=loop, daemon=True, name="consequences").start()
