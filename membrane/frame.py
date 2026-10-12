"""THE WATCH FRAME: entities as axes (2026-10-08, the operator's ask after
the cortex experiment's "a detected entity becomes an axis").

The seat's ATTENTION is its coordinate frame: every entity it watches
(its entity tracks, watch_ops / watch_fields on the store's docs -> that
entity's field state)
and every board watch (its probe direction) is one axis. Each message the
field applies is measured along every axis (cosine to the axis); a FAST and
a SLOW average per axis give its LEAN -- the field talks along this axis
more (or less) than it usually does. The seat picks its axes by picking
what it watches; the engine only measures. A dozen dot products a message
(the attention budget caps the frame at 12 axes).
"""
from __future__ import annotations

import math
import threading
import time

import numpy as np

FAST_HALF_LIFE_S = 15 * 60.0
SLOW_HALF_LIFE_S = 6 * 3600.0
REFRESH_S = 60.0
LEAN_SHOW = 0.05            # the line names axes leaning at least this much
MAX_AXES = 12


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else None


def watched_axes(srv) -> list[tuple[str, str]]:
    """(axis id, sense) for what the seat watches: its entity tracks, entity
    ids from the store's watch_ops / watch_fields, '#<wid> <label>' for
    board watches."""
    out: list[tuple[str, str]] = []
    est = getattr(srv, "entity_store", None)
    # the entity TRACKS first (entity_watch: the seat's main way of
    # watching -- the header's, outside the attention budget)
    for key in list((getattr(est, "tracks", None) or {}).keys()):
        out.append((str(key), "track"))
    for eid, ent in list((getattr(est, "docs", None) or {}).items()):
        props = getattr(ent, "properties", None) or {}
        if props.get("watch_ops") or props.get("watch_fields"):
            out.append((eid, "entity"))
    board = getattr(srv, "_watch_board", None)
    for w in list((getattr(board, "watches", None) or {}).values()):
        out.append((f"#{w.wid} {w.label}", "board"))
    return out[:MAX_AXES]


def _field_state(srv, eid: str):
    fld = getattr(srv, "efield", None)
    if fld is None:
        return None
    bare = eid.split("::", 1)[-1]
    from .eids import field_id
    cands = [field_id(eid), eid, bare, "m:" + bare]
    try:
        from .night import canon_eid
        c = canon_eid(eid)
        if c:
            cands.insert(1, c)
    except Exception:
        pass
    for k in cands:
        try:
            st = fld.get(k)
        except Exception:
            st = None
        if st is not None and getattr(st, "tier", "live") == "live" and getattr(st, "state", None) is not None:
            return st.state
    return None


class Frame:
    def __init__(self, srv, axes_fn=None):
        self.srv = srv
        self.axes_fn = axes_fn or (lambda: watched_axes(srv))
        self.axes: dict[str, dict] = {}       # axis -> {sense, vec, fast, slow, last, n, ts}
        self._lock = threading.Lock()
        self._refreshed = 0.0

    def refresh(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        board = getattr(self.srv, "_watch_board", None)
        probes = {f"#{w.wid} {w.label}": w.probe
                  for w in (getattr(board, "watches", None) or {}).values()}
        fresh = {}
        for aid, sense in self.axes_fn():
            vec = probes.get(aid) if sense == "board" else _field_state(self.srv, aid)
            u = _unit(vec) if vec is not None else None
            if u is None:
                continue
            old = self.axes.get(aid) or {}
            fresh[aid] = {"sense": sense, "vec": u, "fast": old.get("fast"),
                          "slow": old.get("slow"), "last": old.get("last"),
                          "n": old.get("n", 0), "ts": old.get("ts", now)}
        with self._lock:
            self.axes = fresh
        self._refreshed = now

    def observe(self, x_msg, now: float | None = None) -> None:
        """One applied message, measured along every axis."""
        now = time.time() if now is None else now
        if now - self._refreshed > REFRESH_S:
            self.refresh(now)
        x = _unit(x_msg)
        if x is None:
            return
        with self._lock:
            for a in self.axes.values():
                c = float(a["vec"] @ x)
                dt = max(0.0, now - float(a["ts"] or now))
                kf = 1.0 - math.pow(0.5, dt / FAST_HALF_LIFE_S) if a["fast"] is not None else 1.0
                ks = 1.0 - math.pow(0.5, dt / SLOW_HALF_LIFE_S) if a["slow"] is not None else 1.0
                # a message is an impulse: at least a sliver of weight even
                # when two arrive in the same second
                kf, ks = max(kf, 0.15), max(ks, 0.02)
                a["fast"] = c if a["fast"] is None else a["fast"] + kf * (c - a["fast"])
                a["slow"] = c if a["slow"] is None else a["slow"] + ks * (c - a["slow"])
                a["last"], a["n"], a["ts"] = c, a["n"] + 1, now

    def readings(self, now: float | None = None) -> list[dict]:
        with self._lock:
            out = [{"axis": aid, "sense": a["sense"], "now": round(a["last"], 3) if a["last"] is not None else None,
                    "lean": round((a["fast"] or 0.0) - (a["slow"] or 0.0), 3), "n": a["n"]}
                   for aid, a in self.axes.items()]
        return sorted(out, key=lambda r: -abs(r["lean"]))

    def line(self, now: float | None = None) -> str:
        """The WHERE WE ARE line: the axes the field leans along now."""
        rs = [r for r in self.readings(now) if r["n"] >= 3 and abs(r["lean"]) >= LEAN_SHOW]
        if not rs:
            return ""
        bits = [f"{r['axis']} {'+' if r['lean'] > 0 else ''}{r['lean']:.2f}" for r in rs[:5]]
        return ("your frame (what you watch, as axes; + = the talk leans toward it "
                "more than usual): " + " · ".join(bits))


def listener(srv):
    """The field's applied-event listener feeding the frame."""
    def fn(key, ev, x_msg=None):
        fr = getattr(srv, "_frame", None)
        if fr is not None and x_msg is not None:
            fr.observe(x_msg)
    return fn
