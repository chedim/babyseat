"""THE SEAT'S SIDE OF BLURP'S BOUNDARY (#440 part 2).

Blurp runs in its own process (#440). The child reaches the seat through two
routes, and the in-process loop crosses the same boundary through these two
functions (the routes in server.py run them):

- `context(srv)` -- GET /membrane/self/context: the small snapshot of the
  seat state a step reads. Plain JSON: Blurp's own entity (its position),
  `paused`, `nerve_paused` and `cpu_share`; on request (`own=True`) Blurp's
  own entities (itself and anything that is its own) to seed the child's
  mirror; `only="own"` returns just those. One answer is at most
  CONTEXT_BUDGET bytes of JSON.
- `effects(srv, effects)` -- POST /membrane/self/effects: the acts, run in
  the seat in order, one result each: `own` (Blurp's own properties, the
  child's mirror flushed back).

#576 retired the old learner's reads and acts (the current, the things and
spots, reach, nudge, say, the mirror, the audits); the new learner (#576 D2)
brings its own. A facade srv (one with `self_remote`) sends both calls over
HTTP instead of running them here.
"""
from __future__ import annotations

import json
import time

CONTEXT_BUDGET = 256 * 1024     # #440 constant 6, approved: bytes of JSON per context answer


def _SL():
    from . import self_loop as SL
    return SL


def _plain_default(o):
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(o, key=str) if isinstance(o, (set, frozenset)) else list(o)
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


def plain(x):
    """x as plain JSON (what crosses the boundary): numpy scalars and arrays
    as numbers and lists, sets sorted, anything else as its text."""
    return json.loads(json.dumps(x, default=_plain_default))


def _vec(v) -> list | None:
    return None if v is None else [float(x) for x in v]


# ---- the context --------------------------------------------------------------------------

def _size(doc) -> int:
    return len(json.dumps(doc).encode())


def _fit(doc: dict) -> dict:
    """An answer over CONTEXT_BUDGET carries nothing but the note."""
    if _size(doc) <= CONTEXT_BUDGET:
        return doc
    return {"v": doc["v"], "ts": doc["ts"], "over_budget": True}


def _remote(srv):
    """The child's way to the seat (self_proc.SeatClient) when srv is its facade."""
    return getattr(srv, "self_remote", None)


def _is_own(est, ent) -> bool:
    """Blurp itself, one of its methods, or one of their nodes."""
    me = _SL()._eid()
    if ent.eid == me:
        return True
    pp = ent.properties or {}
    if me in (pp.get("method_of") or []):
        return True
    host = (est.docs or {}).get(str(pp.get("node_of") or ""))
    return host is not None and me in ((host.properties or {}).get("method_of") or [])


def _own(srv) -> dict:
    """Blurp's own entities: eid -> {cls, properties, position}."""
    SL = _SL()
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {}
    SL._entity(srv)
    out = {}
    for eid, e in sorted(list((est.docs or {}).items())):
        if _is_own(est, e):
            out[eid] = plain({"cls": e.cls, "properties": e.properties or {},
                              "position": _vec(e.position) if e.position is not None else None})
    return out


def context(srv, only: str | None = None, own: bool = False, report: dict | None = None) -> dict:
    """The snapshot (see the module doc). Plain JSON, at most CONTEXT_BUDGET bytes.
    only="learn" (#576 D2): Blurp's step reports {awake, asleep, mode} and takes
    the net energy its learner spent and earned (ch_blurp.exchange)."""
    r = _remote(srv)
    if r is not None:
        return r.context(only=only, own=own, report=report)
    if only == "learn":
        from . import ch_blurp as _CHB
        return _CHB.exchange(srv, report or {})
    SL = _SL()
    doc: dict = {"v": 1, "ts": time.time()}
    if only != "own":
        ent = SL._entity(srv)
        doc["self"] = {"eid": SL._eid(),
                       "position": _vec(ent.position) if ent is not None and ent.position is not None else None}
        doc["paused"] = bool(getattr(srv, "paused", False))
        doc["nerve_paused"] = bool(SL._paused(srv))
        try:
            doc["cpu_share"] = float(SL.cpu_share(srv))
        except Exception:
            doc["cpu_share"] = None
    if own:
        doc["own"] = _own(srv)
    return _fit(doc)


# ---- the effects --------------------------------------------------------------------------

def _own_effect(srv, e: dict) -> dict:
    """Blurp's own properties (itself, its methods): the keys given replace
    the seat's, `script_data` merged key by key. Never another thing's."""
    est = getattr(srv, "entity_store", None)
    ent = (est.docs or {}).get(str(e.get("eid") or "")) if est is not None else None
    props = e.get("properties")
    if ent is None or not isinstance(props, dict) or not _is_own(est, ent):
        return {"ok": False, "error": "not its own"}
    pp = ent.properties or {}
    for k, v in props.items():
        if k == "script_data" and isinstance(v, dict):
            sd = dict(pp.get("script_data") or {})
            sd.update(v)
            pp[k] = sd
        else:
            pp[k] = v
    ent.properties = pp
    est.upsert(ent, embedder=None)
    return {"ok": True}


EFFECTS = {"own": _own_effect}


def effects(srv, effects) -> list[dict]:
    """Run the effects in the seat, in order: one result each."""
    if not isinstance(effects, list):
        raise ValueError("effects must be a list")
    r = _remote(srv)
    if r is not None:
        return r.effects(effects)
    out = []
    for e in effects:
        fn = EFFECTS.get(e.get("kind")) if isinstance(e, dict) else None
        if fn is None:
            out.append({"ok": False, "error": "unknown effect"})
            continue
        out.append(fn(srv, e))
    return out
