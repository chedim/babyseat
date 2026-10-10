"""STRANGERS: the login page's visitors as TTL'd entities (2026-10-10, the
operator: "make the login page itself and every visitor that looks at it a
TTL'd entity and make the backgrounded piece of the graph follow that
entity on the graph from close distance (so that the whole graph is never
fully visible)").

`site:login` is the page; each visit mints `strangerN` beside it. The page's
background shows only the field's SHAPE within a small radius of its
stranger -- relative positions, masses, edges, pulses; never an id, a label,
a kind or text. The stranger's mouse drifts it through the field (cube space,
no embedding), and a coalesced line ("stranger7 moved 40 times, clicked 2")
reaches the field at most once a minute per stranger.

GUARDRAILS (the operator agreed: "lets not melt the phone"):
- only numbers come in: kinds from a fixed set, counts as ints -- nothing a
  stranger types reaches the field or the seat's context;
- caps: MAX_LIVE strangers, PER_IP_LIVE per address key, a TTL after the
  last sign of life, a hard lifetime, a global field-event budget;
- the projection is the 3D viewer's (built at most every PROJ_FRESH_S here),
  each view a numpy pass over N x 3 -- no ANN, no embedding.
"""
from __future__ import annotations

import random
import secrets
import threading
import time

import numpy as np

SITE = "site:login"
MAX_LIVE = 20
PER_IP_LIVE = 2
TTL_S = 15 * 60.0                 # after the last sign of life
LIFETIME_S = 2 * 3600.0
RADIUS = 0.05                     # the window: cube units around the stranger
MAX_NODES = 150
VIEW_CACHE_S = 3.0
STEP = 0.00015                    # cube units per reported mouse pixel
MAX_STEP = 0.004                  # per event
FIELD_EVERY_S = 60.0              # one coalesced field line per stranger at most
FIELD_BUDGET_PER_MIN = 12         # across all strangers
PROJ_FRESH_S = 15 * 60.0
KINDS = ("move", "click", "stay", "focus", "blur")

_lock = threading.RLock()


def _state(srv) -> dict:
    st = getattr(srv, "_strangers", None)
    if st is None:
        st = srv._strangers = {"by_token": {}, "budget": [], "swept_boot": False}
    return st


def _now() -> float:
    return time.time()


# ---- the projection (the viewer's whole-field 3D fit, reused long) ---------
def _projection(srv):
    by_pid = getattr(srv, "_field3d", None) or {}
    fresh = [p for p in by_pid.values()
             if not getattr(p, "axes", None) or ",".join(a.id for a in p.axes) in ("", "pc1,pc2,pc3")]
    fresh = [p for p in fresh if _now() - p.ts < PROJ_FRESH_S]
    if fresh:
        return max(fresh, key=lambda p: p.ts)
    from .lanes import field3d_projection
    return field3d_projection(srv, "")


# ---- the store side ---------------------------------------------------------
def _est(srv):
    return getattr(srv, "entity_store", None)


def _site(est):
    from .entities import CLASS_ENDPOINT
    d = est._resolve_spec(SITE)
    if d is None:
        r = est.add(cls=CLASS_ENDPOINT, name=SITE,
                    summary="the Home Assistant login page (agatha.boats); its visitors appear "
                            "beside it as strangerN for as long as they look")
        d = (est.docs or {}).get(r.get("eid", "")) if isinstance(r, dict) else None
    return d


def _sweep_boot(srv, st) -> None:
    """Strangers outlive nothing: after a restart their tokens are gone, so
    every stranger entity left in the store goes."""
    if st["swept_boot"]:
        return
    st["swept_boot"] = True
    est = _est(srv)
    if est is None:
        return
    for eid, d in list((est.docs or {}).items()):
        if (d.properties or {}).get("stranger"):
            try:
                est.remove(eid)
            except Exception:
                pass


def _sweep(srv, st, now: float) -> None:
    for tok, s in list(st["by_token"].items()):
        if now - s["last"] > TTL_S or now - s["born"] > LIFETIME_S:
            _end(srv, st, tok, "expired", now)


def _end(srv, st, tok: str, why: str, now: float) -> None:
    s = st["by_token"].pop(tok, None)
    if s is None:
        return
    _flush_field(srv, st, s, now, force=True, closing=why)
    est = _est(srv)
    if est is not None:
        try:
            est.remove(s["eid"])
        except Exception:
            pass


def _field_line(srv, name: str, text: str, now: float) -> bool:
    fld = getattr(srv, "efield", None)
    if fld is None:
        return False
    try:
        from . import streams as ST
        fld.write_events([ST.Event(stream="observation", tool="visit",
                                   args={"entity": name, "site": SITE},
                                   result_head=text, outcome="ok", ts=now, subject=name)],
                         session="strangers")
        return True
    except Exception:
        return False


def _budget_ok(st, now: float) -> bool:
    st["budget"] = [t for t in st["budget"] if now - t < 60.0]
    if len(st["budget"]) >= FIELD_BUDGET_PER_MIN:
        return False
    st["budget"].append(now)
    return True


def _flush_field(srv, st, s, now: float, force: bool = False, closing: str = "") -> None:
    c = s["counts"]
    if not any(c.values()) and not closing:
        return
    if not force and now - s["field_ts"] < FIELD_EVERY_S:
        return
    if not _budget_ok(st, now):
        return
    parts = [f"{k} {v}" for k, v in c.items() if v]
    secs = int(now - s["born"])
    text = (f"{s['name']} on the login page after {secs}s: " + (", ".join(parts) or "still")
            + (f"; left ({closing})" if closing else ""))
    if _field_line(srv, s["name"], text, now):
        s["field_ts"] = now
        s["counts"] = {k: 0 for k in KINDS}


# ---- the four verbs ---------------------------------------------------------
def visit(srv, ip_key: str = "", now: float | None = None) -> dict:
    now = now or _now()
    with _lock:
        st = _state(srv)
        _sweep_boot(srv, st)
        _sweep(srv, st, now)
        live = st["by_token"]
        if len(live) >= MAX_LIVE:
            return {"ok": False, "error": "busy"}
        ip_key = str(ip_key or "")[:32]
        if ip_key and sum(1 for s in live.values() if s["ip"] == ip_key) >= PER_IP_LIVE:
            return {"ok": False, "error": "busy"}
        est = _est(srv)
        if est is None:
            return {"ok": False, "error": "the entity layer is off"}
        site = _site(est)
        sp = (site.properties or {}) if site is not None else {}
        n = int(sp.get("strangers_seen") or 0) + 1
        sp["strangers_seen"] = n
        name = f"stranger{n}"
        from .entities import CLASS_SESSION
        r = est.add(cls=CLASS_SESSION, name=name,
                    summary=f"a visitor looking at the login page (seen {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))})")
        d = (est.docs or {}).get(r.get("eid", "")) if isinstance(r, dict) else None
        if d is None:
            return {"ok": False, "error": "could not mint the stranger"}
        d.properties.update({"stranger": True, "until": now + TTL_S, "parents": [],
                             "visits": SITE})
        try:
            proj = _projection(srv)
        except Exception:
            proj = None
        start = None
        if proj is not None:
            i = proj.index.get(SITE)
            if i is None and site is not None:
                from .classes import short_name
                i = proj.index.get(short_name(site.eid))
            if i is not None:
                start = [float(v) for v in proj.coords[i]]
        if start is None:
            rnd = random.Random(n)
            start = [0.5 + rnd.uniform(-0.15, 0.15) for _ in range(3)]
        jit = random.Random(n * 7919)
        me = [min(1.0, max(0.0, v + jit.uniform(-RADIUS / 3, RADIUS / 3))) for v in start]
        tok = secrets.token_hex(16)
        live[tok] = {"eid": d.eid, "name": name, "ip": ip_key, "born": now, "last": now,
                     "me": me, "counts": {k: 0 for k in KINDS}, "field_ts": now,
                     "view": None, "view_ts": 0.0, "pulse_ts": now}
        if _budget_ok(st, now):
            _field_line(srv, name, f"{name} arrived at the login page", now)
        return {"ok": True, "t": tok, "name": name, "ttl": int(TTL_S)}


def _recent_applied(srv, since: float) -> set:
    try:
        from . import fieldpulse as FP
        fp = FP.of(srv)
        return fp.recent_since(since) if fp is not None else set()
    except Exception:
        return set()


def view(srv, tok: str, now: float | None = None) -> dict:
    """The stranger's window: nodes within RADIUS of it, positions RELATIVE
    to it and scaled to [-1, 1]; masses; edges as index pairs; pulses as
    indices. Nothing else."""
    now = now or _now()
    with _lock:
        st = _state(srv)
        _sweep(srv, st, now)
        s = st["by_token"].get(str(tok or ""))
        if s is None:
            return {"ok": False, "error": "gone"}
        s["last"] = now
        cached = s["view"]
        if cached is None or now - s["view_ts"] > VIEW_CACHE_S:
            try:
                proj = _projection(srv)
            except Exception:
                proj = None
            cached = _window(proj, s["me"]) if proj is not None else {"idx": [], "ids": [], "p": [], "m": [], "e": []}
            s["view"], s["view_ts"] = cached, now
        hot = _recent_applied(srv, s["pulse_ts"])
        s["pulse_ts"] = now
        _flush_field(srv, st, s, now)
        pulses = [k for k, e in enumerate(cached["ids"]) if e in hot]
        self_hot = s["eid"] in hot or any(e.endswith(s["name"]) for e in hot)
        return {"ok": True, "name": s["name"], "p": cached["p"], "m": cached["m"],
                "e": cached["e"], "pulse": pulses, "me_pulse": bool(self_hot),
                "ttl": int(TTL_S - (now - s["last"]))}


def _window(proj, me) -> dict:
    C = np.asarray(proj.coords, dtype=np.float32)
    if not len(C):
        return {"idx": [], "ids": [], "p": [], "m": [], "e": []}
    c = np.asarray(me, dtype=np.float32)
    d = np.abs(C - c).max(axis=1)
    near = np.nonzero(d <= RADIUS)[0]
    if len(near) > MAX_NODES:
        mass = np.asarray(proj.mass)[near]
        near = near[np.argsort(-mass, kind="stable")[:MAX_NODES]]
    near = [int(i) for i in near]
    pos = {i: k for k, i in enumerate(near)}
    p = [[round(float((C[i][a] - c[a]) / RADIUS), 3) for a in range(3)] for i in near]
    m = [int(proj.mass[i]) for i in near]
    edges = set()
    eo = getattr(proj, "_edges_of", {}) or {}
    for i in near:
        for j in eo.get(i, ()):
            sx, tx = proj.edges[j][0], proj.edges[j][1]
            if sx in pos and tx in pos and sx != tx:
                a, b = pos[sx], pos[tx]
                edges.add((min(a, b), max(a, b)))
    return {"idx": near, "ids": [proj.ids[i] for i in near], "p": p, "m": m,
            "e": sorted(edges)[:MAX_NODES * 3]}


def event(srv, tok: str, kind: str, n: int = 1, dx: int = 0, dy: int = 0,
          now: float | None = None) -> dict:
    """A sign of life, numbers only: a kind from KINDS, a count, and for
    'move' the summed pointer motion -- the stranger drifts through the
    field by it (cube space; the window follows)."""
    now = now or _now()
    with _lock:
        st = _state(srv)
        s = st["by_token"].get(str(tok or ""))
        if s is None:
            return {"ok": False, "error": "gone"}
        kind = str(kind or "")
        if kind not in KINDS:
            return {"ok": False, "error": "kind"}
        s["last"] = now
        try:
            n = max(0, min(int(n), 1000))
            dx = max(-2000, min(int(dx), 2000))
            dy = max(-2000, min(int(dy), 2000))
        except (TypeError, ValueError):
            return {"ok": False, "error": "numbers"}
        s["counts"][kind] = s["counts"].get(kind, 0) + n
        if kind == "move" and (dx or dy):
            step = [dx * STEP, -dy * STEP, (dx - dy) * STEP * 0.3]
            norm = max(abs(v) for v in step)
            if norm > MAX_STEP:
                step = [v * MAX_STEP / norm for v in step]
            s["me"] = [min(1.0, max(0.0, a + b)) for a, b in zip(s["me"], step)]
            s["view_ts"] = 0.0 if norm > RADIUS / 10 else s["view_ts"]
        _flush_field(srv, st, s, now)
        return {"ok": True}


def leave(srv, tok: str, now: float | None = None) -> dict:
    now = now or _now()
    with _lock:
        st = _state(srv)
        if str(tok or "") not in st["by_token"]:
            return {"ok": False, "error": "gone"}
        _end(srv, st, str(tok), "closed", now)
        return {"ok": True}


def stats(srv) -> dict:
    st = _state(srv)
    return {"live": len(st["by_token"]),
            "names": [s["name"] for s in st["by_token"].values()]}
