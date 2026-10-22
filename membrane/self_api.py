"""THE SEAT'S SIDE OF BLURP'S BOUNDARY (#440 part 2).

Blurp is moving into its own process (#440). The child reaches the seat
through two routes, and the in-process loop already crosses the same
boundary through these two functions (the routes in server.py run them), so
part 3 can move the loop out unchanged:

- `context(srv)` -- GET /membrane/self/context: the small per-cycle snapshot
  of the seat state a cycle reads. Plain JSON: the current, Blurp's own
  entity (its position), the things in its world (eid; `chat` for a chat
  thing, `merged` for a merged one, `getter` for one it can look at -- no
  vectors), `paused`, `nerve_paused` and `cpu_share`. On request
  (`reach=True`) the field spots it can reach, each by its dimension, and
  (`spots=[eid, ...]`) the vectors of the spots it asks for; `only="spots"`
  returns just those. Never the whole field: one answer is at most
  CONTEXT_BUDGET bytes of JSON -- past it, things, then reach, then spots
  are cut from the end and `dropped` says how many of each.
- `effects(srv, effects)` -- POST /membrane/self/effects: the cycle's acts,
  run in the seat in order, one result each: `position` (its own position,
  and the pull it learned), `nudge` (another thing's own nudge: the toybox,
  or the thing's `nudge` method), `say` (the line that fits the captured
  moves, out through the chat thing).

The seat-side readers stay self_loop's (`_current`, `_spot`, `_targets`,
`_paused`, `cpu_share`): this module calls them, the cycle no longer does.
Reaches (a getter's read) and the cycle's own methods still run in-process;
part 3's facade carries them.
"""
from __future__ import annotations

import json
import time

CONTEXT_BUDGET = 256 * 1024     # #440 constant 6, approved: bytes of JSON per context answer
CUT_ORDER = ("things", "reach", "spots")


def _SL():
    from . import self_loop as SL
    return SL


def _vec(v) -> list | None:
    return None if v is None else [float(x) for x in v]


# ---- the context --------------------------------------------------------------------------

def _things(srv) -> list[dict]:
    """The things in Blurp's world (itself left out), by eid: flags only."""
    SL = _SL()
    est = getattr(srv, "entity_store", None)
    docs = (est.docs if est is not None else None) or {}
    try:
        getters = set(SL._targets(srv)) if est is not None else set()
    except Exception:
        getters = set()
    me = SL._eid()
    out = []
    for eid, e in sorted(list(docs.items())):
        if eid == me or not SL._in_world(srv, eid):
            continue
        pp = e.properties or {}
        t: dict = {"eid": eid}
        if pp.get("merged_into"):
            t["merged"] = True
        if pp.get("chat_io") in ("chat", "out"):
            t["chat"] = True
        if eid in getters:
            t["getter"] = True
        out.append(t)
    return out


def _reach(srv) -> dict:
    """The field spots Blurp can reach: eid -> dimension (in its world, never
    merged, never itself) -- self_loop's old _spots, without the vectors."""
    SL = _SL()
    est = getattr(srv, "entity_store", None)
    out = {}
    for eid, e in sorted(list(((est.docs if est is not None else None) or {}).items())):
        if eid == SL._eid() or (e.properties or {}).get("merged_into") or not SL._in_world(srv, eid):
            continue
        v = SL._spot(srv, eid)
        if v is not None:
            out[eid] = len(v)
    return out


def _spots(srv, eids) -> dict:
    SL = _SL()
    out = {}
    for eid in dict.fromkeys(str(e) for e in (eids or ()) if e):
        v = SL._spot(srv, eid)
        if v is not None:
            out[eid] = _vec(v)
    return out


def _size(doc) -> int:
    return len(json.dumps(doc).encode())


def _fit(doc: dict) -> dict:
    """Cut the open-ended parts until the answer fits CONTEXT_BUDGET."""
    over = _size(doc) - CONTEXT_BUDGET
    if over <= 0:
        return doc
    dropped = doc.setdefault("dropped", {})
    over += _size({"dropped": {k: 10 ** 9 for k in CUT_ORDER}})      # room for the note itself
    for key in CUT_ORDER:
        part = doc.get(key)
        if not part or over <= 0:
            continue
        items = list(part.items()) if isinstance(part, dict) else list(part)
        n = 0
        while items and over > 0:
            it = items.pop()
            over -= _size({it[0]: it[1]} if isinstance(part, dict) else [it]) - 1
            n += 1
        doc[key] = dict(items) if isinstance(part, dict) else items
        dropped[key] = n
    if _size(doc) > CONTEXT_BUDGET:                 # the cycle's own part alone is too big: say so, carry nothing
        return {"v": doc["v"], "ts": doc["ts"], "over_budget": True, "dropped": dropped}
    return doc


def context(srv, spots=(), reach: bool = False, only: str | None = None) -> dict:
    """The per-cycle snapshot (see the module doc). Plain JSON, at most
    CONTEXT_BUDGET bytes."""
    SL = _SL()
    doc: dict = {"v": 1, "ts": time.time()}
    if only != "spots":
        ent = SL._entity(srv)
        doc["self"] = {"eid": SL._eid(),
                       "position": _vec(ent.position) if ent is not None and ent.position is not None else None}
        doc["current"] = _vec(SL._current(srv))
        doc["paused"] = bool(getattr(srv, "paused", False))
        doc["nerve_paused"] = bool(SL._paused(srv))
        try:
            doc["cpu_share"] = float(SL.cpu_share(srv))
        except Exception:
            doc["cpu_share"] = None
        doc["things"] = _things(srv)
    if reach:
        doc["reach"] = _reach(srv)
    doc["spots"] = _spots(srv, spots)
    return _fit(doc)


# ---- the effects --------------------------------------------------------------------------

def _position(srv, e: dict) -> dict:
    SL = _SL()
    est = getattr(srv, "entity_store", None)
    ent = SL._entity(srv)
    if est is None or ent is None:
        return {"ok": False, "error": "no entity store"}
    pos = e.get("position")
    ent.position = None if pos is None else [float(x) for x in pos]
    if "pull" in e:
        pp = ent.properties or {}
        sd = dict(pp.get("script_data") or {})
        sd.pop("current_model", None)            # the old home (cut to 128 items on save)
        sd["pull"] = e["pull"]
        pp["script_data"] = sd
        ent.properties = pp
    est.upsert(ent, embedder=None)
    return {"ok": True}


def _nudge(srv, e: dict) -> dict:
    """Another thing's own nudge, in the seat: it decides what a nudge does.
    -> {rc: NUDGE_*, did, text}."""
    SL = _SL()
    est = srv.entity_store
    target = str(e.get("eid") or "")
    ent = (est.docs or {}).get(target)
    if ent is None:
        return {"rc": SL.NUDGE_NOTHING, "did": "nothing there", "text": "nothing there"}
    if not SL._in_world(srv, target):
        return {"rc": SL.NUDGE_REFUSED, "did": "outside its world", "text": "not in its world"}
    tb = (ent.properties or {}).get("toybox") or {}
    if tb.get("name"):
        from . import toybox as _TB
        try:
            out = _TB.nudge(tb["name"], block=tb.get("block"), now=e.get("now"))
        except Exception as ex:                              # noqa: BLE001
            out = {"ok": False, "refused": False, "changed": False, "error": repr(ex)}
        rc = (SL.NUDGE_REFUSED if out.get("refused") else SL.NUDGE_MOVED if out.get("changed")
              else SL.NUDGE_NOTHING)
        return {"rc": rc, "did": str(out.get("did") or out.get("error") or ""), "text": "nudged"}
    from .night import method_call, resolve_method
    if resolve_method(est, ent, "nudge")[0] is None:
        return {"rc": SL.NUDGE_NOTHING, "did": "it takes no nudge", "text": "nudged"}
    out = method_call(srv, est, None, None, target, "nudge", {})
    return {"rc": SL.NUDGE_MOVED if out.get("rc") == 0 else SL.NUDGE_REFUSED,
            "did": str(out.get("head") or out.get("error") or out.get("outcome") or "")[:120],
            "text": "nudged"}


def _say(srv, e: dict) -> dict:
    """The line for the captured moves (`sum`): its fit over the sentences
    heard in the person's private chat, out through the chat thing as
    Blurp's own. -> {said: [sid], posted, why}; said [] when nothing fits."""
    SL = _SL()
    from . import chat_io as CIO
    ent = (srv.entity_store.docs or {}).get(str(e.get("eid") or ""))
    if ent is None:
        return {"said": [], "posted": False, "why": "nothing there"}
    person = str((ent.properties or {}).get("person") or "")
    picked = SL._compose(srv, e.get("sum"), CIO.blurp_tag(person))
    if not picked:
        return {"said": [], "posted": False, "why": "nothing fits"}
    pp = ent.properties or {}
    pp["say"] = " ".join(t for _, t in picked)
    ent.properties = pp
    out = CIO.on_edit(srv, ent, by="self") or {"posted": False, "why": "empty"}
    return {"said": [sid for sid, _ in picked], "posted": bool(out.get("posted")),
            "why": str(out.get("why") or "")}


EFFECTS = {"position": _position, "nudge": _nudge, "say": _say}


def effects(srv, effects) -> list[dict]:
    """Run the cycle's effects in the seat, in order: one result each."""
    if not isinstance(effects, list):
        raise ValueError("effects must be a list")
    out = []
    for e in effects:
        fn = EFFECTS.get(e.get("kind")) if isinstance(e, dict) else None
        if fn is None:
            out.append({"ok": False, "error": "unknown effect"})
            continue
        out.append(fn(srv, e))
    return out
