"""#1101 (operator 2026-11-11): unknown tool names call cliffhanger methods (__call), resolved through a context
stack, like scopes in code.

- CHAT FRAME (persistent, like `cd`): `entity enter <spec>` / `entity exit` push/pop entities on the chat's stack;
  it stays with the chat (kept in .membrane/ctx-chat.json) until changed.
- TURN FRAME (lexical, on top): every entity the model reads or writes this turn; innermost = last touched;
  dropped when the next person turn starts.
- RESOLUTION of a bare method name, innermost first: turn frame -> chat frame (innermost entered first) -> the
  chat's own entities (made in this chat) -> the seat (global methods). Two owners at one level -> a miss that
  names both. Never creates anything.
- `in: chat › <entered…> › <last touched>` -- one line in the model's context.

The "current chat" of a tool call is the chat whose turn the seat staged last (a tool call carries no chat id).

PRONOUNS ARE SCOPED in the same stack (operator 2026-11-11), innermost first, like variables:
- turn frame: it/this/that = the last CONCRETE entity touched this turn; they/them/these/those = the concrete ones
  touched this turn; he/him/she/her = the last person touched;
- entered frames: an entered entity binds it/this/that to itself, and what is touched while it is the innermost
  frame rebinds there; exit drops its bindings, so the outer ones are back;
- the person/chat level: convgraph's registers (the flat ones, with their own expiry) -- not a second store.
Only concrete entities bind (files, symbols, goals/tasks/decisions, msg:, person:, Command:/File:, named things),
never a generic concept. "there/here" bind only a place entity; "then" binds nothing."""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()
TURN_MAX = 8


def _st(srv) -> dict:
    d = srv.__dict__.setdefault("_callctx", None)
    if d is None:
        d = {"cur": "", "turn": {}, "chat": _load(srv)}
        srv.__dict__["_callctx"] = d
    return d


def _path(srv) -> str:
    base = getattr(srv, "data_dir", None) or ".membrane"
    return os.path.join(str(base), "ctx-chat.json")


def _load(srv) -> dict:
    try:
        with open(_path(srv)) as f:
            return dict(json.load(f))
    except (OSError, ValueError):
        return {}


def _save(srv, chat: dict) -> None:
    try:
        with open(_path(srv), "w") as f:
            json.dump(chat, f)
    except OSError:
        pass


def new_turn(srv, sid: str) -> None:
    """A person's turn starts in chat `sid`: it becomes the current chat; its turn frame is empty."""
    with _LOCK:
        s = _st(srv)
        s["cur"] = str(sid or "")
        s["turn"][s["cur"]] = []


def current(srv) -> str:
    return _st(srv)["cur"]


SINGULAR = {"it", "this", "that"}
PLURAL = {"they", "them", "these", "those"}
PERSONAL = {"he", "him", "she", "her"}
PLACE = {"there", "here"}
PRONOUNS = SINGULAR | PLURAL | PERSONAL | PLACE


def _short(eid: str) -> str:
    return str(eid).replace("membrane::", "")


def concrete(eid: str) -> bool:
    """Only concrete things bind a pronoun -- never a generic concept (or a word, a session, a register)."""
    e = _short(eid).lower()
    return bool(e) and not e.startswith(("concept:", "w:", "word:", "register:", "session:", "s:?", "seat:"))


def _person(eid: str) -> bool:
    return _short(eid).lower().startswith(("person:", "p:"))


def _place(eid: str) -> bool:
    return _short(eid).lower().startswith(("place:", "loc:", "location:"))


def _frames(s: dict, k: str) -> list:
    """The chat stack's frames as dicts {eid, bind} (old saves were bare eids)."""
    fr = s["chat"].setdefault(k, [])
    for i, f in enumerate(fr):
        if isinstance(f, str):
            fr[i] = {"eid": f, "bind": {"it": f}}
    return fr


def _bind_touch(bind: dict, eid: str) -> None:
    if _person(eid):
        bind["he"] = eid
    elif _place(eid):
        bind["there"] = eid
    else:
        bind["it"] = eid


def touch(srv, eid: str, sid: str | None = None) -> None:
    eid = str(eid or "").strip()
    if not eid:
        return
    with _LOCK:
        s = _st(srv)
        k = sid if sid is not None else s["cur"]
        fr = s["turn"].setdefault(k, [])
        if eid in fr:
            fr.remove(eid)
        fr.append(eid)
        del fr[:-TURN_MAX]
        frames = _frames(s, k)
        if frames and concrete(eid):              # the innermost entered frame rebinds while it is innermost
            _bind_touch(frames[-1]["bind"], eid)
            _save(srv, s["chat"])


def enter(srv, eid: str, sid: str | None = None) -> list:
    with _LOCK:
        s = _st(srv)
        k = sid if sid is not None else s["cur"]
        fr = _frames(s, k)
        fr[:] = [f for f in fr if f["eid"] != eid]
        fr.append({"eid": eid, "bind": ({"it": eid} if concrete(eid) and not _person(eid) else
                                        {"he": eid} if _person(eid) else {})})
        _save(srv, s["chat"])
        return [f["eid"] for f in fr]


def exit_(srv, sid: str | None = None) -> list:
    with _LOCK:
        s = _st(srv)
        k = sid if sid is not None else s["cur"]
        fr = _frames(s, k)
        if fr:
            fr.pop()                              # its bindings go with it: the outer ones are back
        _save(srv, s["chat"])
        return [f["eid"] for f in fr]


def _key(word: str) -> str:
    w = str(word or "").lower().strip()
    if w in SINGULAR:
        return "it"
    if w in PLURAL:
        return "they"
    if w in PERSONAL:
        return "he"
    if w in PLACE:
        return "there"
    return ""


def pronoun(srv, word: str, sid: str | None = None):
    """What a pronoun means here, innermost first: turn frame -> entered frames -> the person/chat registers.
    One eid, a list (they), or None."""
    key = _key(word)
    if not key:
        return None
    s = _st(srv)
    k = sid if sid is not None else s["cur"]
    turn = [e for e in s["turn"].get(k, []) if concrete(e)]
    if key == "they":
        if len(turn) > 1:
            return list(reversed(turn))
    else:
        for e in reversed(turn):
            if (key == "he" and _person(e)) or (key == "there" and _place(e)) or \
                    (key == "it" and not _person(e) and not _place(e)):
                return e
    for f in reversed(_frames(s, k)):
        b = f.get("bind") or {}
        if b.get(key):
            return b[key]
    try:                                          # the person/chat level: convgraph's registers (their expiry)
        from .convgraph import register
        got = register(srv, k, word)
        if got:
            got = list(got) if isinstance(got, (list, tuple)) else got
            if key == "they":
                return got if isinstance(got, list) else [got]
            one = got[0] if isinstance(got, list) else got
            return one if concrete(str(one)) else None
    except Exception:
        pass
    return None


def held(srv, sid: str | None = None) -> list:
    """#1105: what the chat's stack holds -- its entered frames, then this turn's touched -- for the map's rows."""
    s = _st(srv)
    k = sid if sid is not None else s["cur"]
    out = [f["eid"] for f in _frames(s, k)] + list(s["turn"].get(k, []))
    return list(dict.fromkeys(out))


def line(srv, sid: str | None = None) -> str:
    s = _st(srv)
    k = sid if sid is not None else s["cur"]
    frames = _frames(s, k)
    parts = ["chat"] + [_short(f["eid"]) for f in frames]
    shown = []
    if s["turn"].get(k) or frames:                # the bindings that are set (registers alone don't show)
        for w in ("it", "they", "he", "there"):
            v = pronoun(srv, w, k)
            if not v:
                continue
            v = ", ".join(_short(x) for x in v[:3]) if isinstance(v, list) else _short(v)
            if v != parts[-1]:                    # entering X makes it=X: no need to say so
                shown.append(f"{w}={v}")
    return "in: " + " › ".join(parts) + (f" ({'; '.join(shown)})" if shown else "")


def _doc(est, spec: str):
    try:
        return est._resolve_spec(spec)
    except Exception:
        return None


def _chat_own(est, sid: str) -> list:
    out = []
    for d in list((est.docs or {}).values()):
        pp = d.properties or {}
        if sid and (pp.get("sid") == sid or pp.get("chat") == sid or pp.get("session") == sid) \
                and not str(d.eid).startswith("membrane::msg:"):
            out.append(d)
    return out


def resolve(srv, method: str, sid: str | None = None) -> dict:
    """{"object": eid, "level": ...} or {"miss": text}."""
    from .night import resolve_method, methods_of
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {"miss": "the entity layer is unavailable"}
    s = _st(srv)
    k = sid if sid is not None else s["cur"]
    levels = [("this turn", list(reversed(s["turn"].get(k, [])))),
              ("this chat's stack", [f["eid"] for f in reversed(_frames(s, k))])]
    reach: list[str] = []
    for label, specs in levels:
        for spec in specs:                       # innermost first: the first owner at this level wins,
            d = _doc(est, spec)                  # unless the SAME position ties (never: one entity each)
            if d is None:
                continue
            m, owner = resolve_method(est, d, method)
            reach += [f"{n} (on {_short(d.eid)})" for n in methods_of(est, d)]
            if m is not None and owner is not None:
                return {"object": d.eid, "level": label}
    own = [d for d in _chat_own(est, k)]
    hits = []
    for d in own:
        m, owner = resolve_method(est, d, method)
        if m is not None and owner is not None:
            hits.append(d.eid)
    if len(hits) == 1:
        return {"object": hits[0], "level": "this chat's entities"}
    if len(hits) > 1:
        return {"miss": f"`{method}` is a method of more than one entity in this chat: "
                        f"{', '.join(_short(h) for h in hits[:4])} -- say which (entity enter <one>, then call it)"}
    owners = {}
    for d in list((est.docs or {}).values()):
        pp = d.properties or {}
        if pp.get("method_name") == method and not pp.get("retired") and not pp.get("abstract"):
            for o in (pp.get("method_of") or ["(global)"]):
                owners[o] = d.eid
    if "(global)" in owners:
        seat = _doc(est, "presence")
        return {"object": seat.eid if seat is not None else "presence", "level": "the seat"}
    if len(owners) == 1:
        return {"object": next(iter(owners)), "level": "the seat"}
    if len(owners) > 1:
        return {"miss": f"`{method}` is a method of {', '.join(_short(o) for o in list(owners)[:4])}: "
                        "say which (entity enter <one>, then call it)"}
    reach = sorted(set(reach))[:12]
    return {"miss": f"no tool or method `{method}`; methods you have here: "
                    f"{', '.join(reach) if reach else '(none in this context)'}; or define one with exec define"}
