"""THE SEAT'S SIDE OF BLURP'S BOUNDARY (#440 part 2).

Blurp is moving into its own process (#440). The child reaches the seat
through two routes, and the in-process loop already crosses the same
boundary through these two functions (the routes in server.py run them), so
part 3 can move the loop out unchanged:

- `context(srv)` -- GET /membrane/self/context: the small per-cycle snapshot
  of the seat state a cycle reads. Plain JSON: the current, Blurp's own
  entity (its position), the things in its world (eid; `chat` for a chat
  thing, `merged` for a merged one, `getter` for one it can look at -- no
  vectors), `targets` (the eids it can look at), `paused`, `nerve_paused`
  and `cpu_share`. On request
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

Part 3 (membrane.self_proc) adds what the child needs besides: `own=True`
in the context (Blurp's own entities -- itself and its methods -- to seed
the child's mirror), `viewer` (whether the seat's viewer watches), and the
effects `reach` (one look: the getter fires in the seat), `own` (Blurp's own
properties and its methods' run counts), `place`, `provision`, `getter` (the
audits' pause of a thing's getter) and `show` (the cycle onto the viewer).
The in-process loop crosses through the same ones. A facade srv (one with
`self_remote`) sends both calls over HTTP instead of running them here.

Part 4 moves the reply checks and the mirror feed across too: `facts` (a
thing's heard_ts and llm_answer, the mirror toy's name), `ask` (rung 8's
question to the model) and `mirror_feed` (the cycle's move, to the mirror).
"""
from __future__ import annotations

import json
import time

CONTEXT_BUDGET = 256 * 1024     # #440 constant 6, approved: bytes of JSON per context answer
CUT_ORDER = ("things", "reach", "spots")


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

def _getters(srv) -> list[str]:
    """What Blurp can look at (self_loop._targets: its world's things with a getter)."""
    try:
        return sorted(_SL()._targets(srv)) if getattr(srv, "entity_store", None) is not None else []
    except Exception:
        return []


def _things(srv, getters=None) -> list[dict]:
    """The things in Blurp's world (itself left out), by eid: flags only."""
    SL = _SL()
    est = getattr(srv, "entity_store", None)
    docs = (est.docs if est is not None else None) or {}
    getters = set(_getters(srv) if getters is None else getters)
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


def context(srv, spots=(), reach: bool = False, only: str | None = None, own: bool = False) -> dict:
    """The per-cycle snapshot (see the module doc). Plain JSON, at most
    CONTEXT_BUDGET bytes."""
    r = _remote(srv)
    if r is not None:
        return r.context(spots=list(spots or ()), reach=reach, only=only, own=own)
    SL = _SL()
    doc: dict = {"v": 1, "ts": time.time()}
    if only not in ("spots", "own"):
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
        from . import fieldpulse as _FP
        doc["viewer"] = _FP.of(srv) is not None
        doc["targets"] = _getters(srv)          # #440 part 3: what its looks pick from
        doc["things"] = _things(srv, doc["targets"])
    if own:
        doc["own"] = _own(srv)
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


def _reach_effect(srv, e: dict) -> dict:
    """One look at a thing, in the seat: its getter fires (or answers that it
    can't). -> {read, toy?, text}; `toy` (a toybox thing) carries its log's
    length before and after the look and who changed it since `since` (the
    log length at Blurp's previous look at it; None: none)."""
    SL = _SL()
    target = str(e.get("eid") or "")
    ent = (srv.entity_store.docs or {}).get(target)
    if ent is None:
        return {"read": {"fired": False, "reason": "gone"}, "text": "nothing there"}
    if not SL._in_world(srv, target):                      # #337: outside its world, nothing is read
        return {"read": {"fired": False, "reason": "outside its world"}, "text": "not in its world"}
    tb = (ent.properties or {}).get("toybox") or {}
    toy = None
    if tb.get("name"):                                      # #244: the toy's log, before and after the look
        from . import toybox as _TB
        toy = {"name": tb["name"], "block": tb.get("block"), "at0": _TB.log_len(tb["name"])}
    try:
        read = SL._read_entity(srv, ent, e.get("now"))
    except Exception as ex:                                 # noqa: BLE001
        read = {"fired": False, "reason": f"error: {ex!r}"[:80]}
    out = {"read": plain(read or {}), "text": "reached"}
    if toy is not None:
        toy["at1"] = _TB.log_len(tb["name"])
        try:
            toy["by"] = SL._toy_by_since(e.get("since"), toy)
        except Exception:
            toy["by"] = None
        out["toy"] = toy
    return out


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


def _place(srv, e: dict) -> dict:
    """The first look places (#244): a field spot for the thing."""
    from . import toybox as _TB
    return {"placed": _TB.place(srv, str(e.get("eid") or ""))}


def _provision(srv, e: dict) -> dict:
    """Switch on the unlocked toys the seat has none of (toy_unlocks)."""
    from . import toy_unlocks as _TU
    return {"added": list(_TU.provision_here(srv, [str(k) for k in e.get("toys") or []], now=e.get("now")))}


def _getter(srv, e: dict) -> dict:
    """The blind audits: a thing's getter paused until `until` (None: resumed)."""
    from . import watch_agents as _WA
    eid = str(e.get("eid") or "")
    if e.get("until") is None:
        _WA.unpause_getter(eid)
    else:
        _WA.pause_getter(eid, float(e["until"]))
    return {"ok": True}


def _show(srv, e: dict) -> dict:
    """The cycle onto the seat's viewer (fieldpulse.self_cycle)."""
    import numpy as np
    from . import fieldpulse as _FP
    fp = _FP.of(srv)
    if fp is None:
        return {"ok": False, "error": "no viewer"}
    pts = {k: (None if v is None else np.asarray(v, dtype=float)) for k, v in (e.get("points") or {}).items()}
    fp.self_cycle(pts, dict(e.get("stats") or {}))
    return {"ok": True}


FACT_KEYS = ("heard_ts", "llm_answer")


def _facts(srv, e: dict) -> dict:
    """Part 4: what the reply checks and the mirror read of the seat. `eid`:
    that thing's FACT_KEYS (when it last spoke, the model's answer about it;
    `props` None when there is no such thing); `mirror`: the mirror toy self
    feeds (self_loop._mirror_toy_here; None without one)."""
    out: dict = {}
    if e.get("eid"):
        est = getattr(srv, "entity_store", None)
        doc = (est.docs or {}).get(str(e["eid"])) if est is not None else None
        pp = (doc.properties or {}) if doc is not None else {}
        out["props"] = plain({k: pp[k] for k in FACT_KEYS if k in pp}) if doc is not None else None
    if e.get("mirror"):
        out["mirror"] = _SL()._mirror_toy_here(srv)
    return out


def _ask(srv, e: dict) -> dict:
    """Part 4: rung 8's question to the model, asked in the seat (llm.request:
    its caps and its lanes are the seat's; the answer lands on the thing)."""
    from . import llm as _LLM
    out = _LLM.request(srv, str(e.get("prompt") or ""), about=str(e.get("eid") or ""), caller="self", readonly=True)
    return plain(out) if isinstance(out, dict) else {"rc": None, "text": str(out)}


def _mirror_feed(srv, e: dict) -> dict:
    """Part 4: this cycle's move, fed to the mirror toy in the seat (toybox.mirror_feed)."""
    from . import toybox as _TB
    return plain(_TB.mirror_feed(str(e.get("toy") or ""), float(e.get("s") or 0.0), now=e.get("now")))


EFFECTS = {"position": _position, "nudge": _nudge, "say": _say, "reach": _reach_effect, "own": _own_effect,
           "place": _place, "provision": _provision, "getter": _getter, "show": _show,
           "facts": _facts, "ask": _ask, "mirror_feed": _mirror_feed}


def effects(srv, effects) -> list[dict]:
    """Run the cycle's effects in the seat, in order: one result each."""
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
