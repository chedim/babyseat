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

The "current chat" of a tool call is the chat whose turn the seat staged last (a tool call carries no chat id)."""
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


def touch(srv, eid: str, sid: str | None = None) -> None:
    eid = str(eid or "").strip()
    if not eid:
        return
    with _LOCK:
        s = _st(srv)
        fr = s["turn"].setdefault(sid if sid is not None else s["cur"], [])
        if eid in fr:
            fr.remove(eid)
        fr.append(eid)
        del fr[:-TURN_MAX]


def enter(srv, eid: str, sid: str | None = None) -> list:
    with _LOCK:
        s = _st(srv)
        k = sid if sid is not None else s["cur"]
        fr = s["chat"].setdefault(k, [])
        if eid in fr:
            fr.remove(eid)
        fr.append(eid)
        _save(srv, s["chat"])
        return list(fr)


def exit_(srv, sid: str | None = None) -> list:
    with _LOCK:
        s = _st(srv)
        k = sid if sid is not None else s["cur"]
        fr = s["chat"].setdefault(k, [])
        if fr:
            fr.pop()
        _save(srv, s["chat"])
        return list(fr)


def _short(eid: str) -> str:
    return str(eid).replace("membrane::", "")


def line(srv, sid: str | None = None) -> str:
    s = _st(srv)
    k = sid if sid is not None else s["cur"]
    parts = ["chat"] + [_short(e) for e in s["chat"].get(k, [])]
    turn = s["turn"].get(k, [])
    if turn and _short(turn[-1]) != parts[-1]:
        parts.append(_short(turn[-1]))
    return "in: " + " › ".join(parts)


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
              ("this chat's stack", list(reversed(s["chat"].get(k, []))))]
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
