"""BLURP'S WORLD (#337): a scope inside the field, never a wipe of it.

Blurp lives in a world of its own inside its seat's field. The field stays
whole and shared (it is the person's memory: their agents' codebase, their
home); Blurp's world is a MEMBERSHIP LIST of canonical field ids
(eids.field_id: a store eid "membrane::Component:toy:ball" and the field's
"m:component:toy:ball" or a word "w:ball" compare in one space), kept with
Blurp at <self dir>/world.json, that ONLY GROWS:

    {"born": <ts>, "members": {fid: {"ts": ..., "why": ...}}}

WHO IS IN IT (`why`):
  birth     a new Blurp (no record yet): itself, and the chat thing
            (chat_io: chat:<person>) of its person (whoever chats on the
            seat, else the operator);
  migrated  a Blurp that already has a record when its world is first made:
            itself, every person's chat entities, and every toybox toy it
            has -- so the live Blurp keeps its toys until the reset; and
            (#333 part 3a) a person's chat thing when it folds in their old
            chat:in / chat:out (chat_io.migrate: the two stay members);
  toy       a toy, when it is provisioned (toybox.add: an unlock, or the
            operator's own add);
  chat      a person's chat thing when they open Blurp's private chat, and
            what a message in that chat (the person's or Blurp's own)
            mentions;
  heard     BLURP HEARS THE ROOM (the operator, 2026-10-21: a room with a
            baby and two adults, the person and the LLM): what any discourse
            event the field applies after the world's birth mentions -- the
            person's messages and the model's prose, in any chat -- and a
            person heard speaking brings their own chat thing;
  seen      ...and SEES WHAT THE ADULTS DO (the operator, 2026-10-21): what a
            tool call (an observation event) applied after the birth
            mentions or touches, in any session.
            The model's reasoning is neither said aloud nor done and adds
            nothing; an event from before the world's birth is the room's
            past and is never heard or seen (a migrated world is born at its
            migration).

The field links every message to the entities it mentions (the event's
`mentions`, already field ids); those links are the membership rule. The
field hands each applied event to its `event_listeners` (efield._apply);
bind() registers on_event there.

Until #576 every read and act of the old learner was filtered through it
(its looks, aims, nudges, the mirror, the audits); that learner retired, and
the world is kept (it grows as before) for the new one."""
from __future__ import annotations

import json
import threading
import time

FILE = "world.json"
HEARD_ROLES = ("user", "model", "self")   # speech in the room: the person's words, the model's prose, Blurp's own lines
SEEN_STREAM = "observation"         # what the adults do: their tool calls
BLURP_SESSION = "blurp:"            # the capture tag of Blurp's private chat (chat_io.blurp_tag)

_lock = threading.RLock()


def _path(srv):
    from . import self_loop as SL
    d = SL._dir(srv)
    return d / FILE if d is not None else None


def _fid(eid: str) -> str:
    from .eids import field_id
    return field_id(eid)


def _save(srv, doc: dict) -> None:
    p = _path(srv)
    if p is None:
        return                                       # no data dir: the world lives in memory only
    try:
        from . import self_writer as _W                # #440: the one writer for self/
        _W.write_json(p, doc, sort_keys=True)
    except Exception:
        pass


def _load(srv) -> dict | None:
    p = _path(srv)
    if p is None or not p.exists():
        return None
    try:
        doc = json.loads(p.read_text())
    except Exception:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("members"), dict):
        return None
    return doc


def _chat_pair(person: str) -> list[str]:
    """A person's chat in Blurp's world: their one chat thing (#333 part 3a;
    the chat:in / chat:out pair until then), as chat_io names it."""
    from . import chat_io as CIO
    return [CIO.eid_of(person)]


def _first(srv, now: float) -> dict:
    """The world's first day: born (a fresh Blurp), or migrated (one that
    already has a record)."""
    from . import chat_io as CIO
    from . import self_loop as SL
    try:
        person = CIO.recent_person(srv)
    except Exception:
        person = ""
    eids = [SL._eid()] + _chat_pair(person)
    why = "birth"
    if SL._read(srv):
        why = "migrated"
        est = getattr(srv, "entity_store", None)
        for eid, e in list(((est.docs if est is not None else None) or {}).items()):
            pp = e.properties or {}
            if pp.get("chat_io") in ("in", "out", "chat") or (pp.get("toybox") or {}).get("kind"):
                eids.append(eid)
    members = {}
    for e in eids:
        f = _fid(e)
        if f:
            members.setdefault(f, {"ts": now, "why": why})
    return {"born": now, "members": members}


def _doc(srv, now: float | None = None) -> dict:
    """The world (made on first use), cached on self's runtime."""
    from . import self_loop as SL
    rt = SL._rt(srv)
    with _lock:
        doc = rt.get("world")
        if doc is None:
            doc = _load(srv)
            if doc is None:
                doc = _first(srv, time.time() if now is None else float(now))
                _save(srv, doc)
            rt["world"] = doc
        return doc


def born(srv) -> float:
    return float(_doc(srv).get("born") or 0.0)


def members(srv) -> set:
    """Blurp's world: the canonical field ids of its members."""
    with _lock:
        return set(_doc(srv)["members"])


def has(srv, eid: str) -> bool:
    """Is this thing (any spelling: a store eid or a field id) in Blurp's world?"""
    f = _fid(eid)
    return bool(f) and f in _doc(srv)["members"]


def join(srv, eids, why: str, now: float | None = None) -> list[str]:
    """Add members (grow-only: a member keeps its first reason). -> the field
    ids that joined."""
    now = time.time() if now is None else float(now)
    with _lock:
        doc = _doc(srv, now)
        got = []
        for e in eids or ():
            f = _fid(e)
            if f and f not in doc["members"]:
                doc["members"][f] = {"ts": now, "why": str(why)}
                got.append(f)
        if got:
            _save(srv, doc)
        return got


def join_person(srv, person: str, why: str = "chat") -> list[str]:
    """A person's chat thing joins (they opened Blurp's private chat)."""
    return join(srv, _chat_pair(person), why)


def on_event(srv, ev: dict) -> list[str]:
    """BLURP HEARS AND SEES THE ROOM: one event the field applied -> its
    mentions join -- speech (the discourse stream, the person's and the
    model's words) as `chat` from Blurp's private chat, else `heard`; an
    action (a tool call's observation) as `seen`. Only after the world's
    birth."""
    if not isinstance(ev, dict):
        return []
    seen = ev.get("stream") == SEEN_STREAM
    if not seen and (ev.get("stream") != "discourse" or ev.get("role") not in HEARD_ROLES):
        return []
    ts = ev.get("ts")
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return []                                    # an untimed (backfilled) event is the room's past
    if ts < born(srv):
        return []
    ids = [m[0] for m in ev.get("mentions") or [] if isinstance(m, (list, tuple)) and m and m[0]]
    spk = ev.get("speaker")                          # a person heard speaking brings their chat (the operator, 2026-10-21)
    if not seen and isinstance(spk, (list, tuple)) and spk and str(spk[0]).startswith("person:"):
        ids += _chat_pair(str(spk[0]).split(":", 1)[1])
    if not ids:
        return []
    why = ("seen" if seen else
           "chat" if str(ev.get("session") or "").startswith(BLURP_SESSION) else "heard")
    return join(srv, ids, why, now=time.time())


def bind(srv) -> None:
    """Boot: the world exists (born or migrated), and the field hands it what
    it applies (once per field)."""
    _doc(srv)
    fld = getattr(srv, "efield", None)
    hooks = getattr(fld, "event_listeners", None)
    if hooks is None:
        return
    from . import self_loop as SL
    rt = SL._rt(srv)
    if rt.get("world_hook") is not None and rt["world_hook"][0] is fld:
        return

    def _heard(key, ev, _srv=srv):
        try:
            on_event(_srv, ev)
        except Exception:
            pass
    hooks.append(_heard)
    rt["world_hook"] = (fld, _heard)
