"""THE MODEL AS AN ENTITY (2026-10-11, the operator: "can we replace our
enforced synthetic llm calls with a more natural 'call-llm' entity?").

membrane::llm is an actuator like any other: its method `call(about,
prompt)` wakes a model turn -- a loop with the listener's hands -- on the
entity `about`, with that prompt as its task. Whoever decides the model is
needed calls it: Argus (exec(action='call', object='llm', method='call',
args={about, prompt})), a script (membrane.call_llm(prompt) -- one per
fire, like speak), and, step by step, the seat's own triggers (a watch's
reaction, auto-chat, the nudges) instead of composing prompts in code.

It returns at once (queued); the turn runs under the loops' rules -- one
loop per entity, the cap, the pause switch -- and its verdict lands on the
entity (watch_last_decision / watch_decisions). The calls are counted and
capped: CALLER_PER_HOUR per caller, GLOBAL_PER_HOUR in all, so no script
loops the model into a bill. The entity keeps the last calls (who, about
what, why): the field can see what it costs.
"""
from __future__ import annotations

import shlex
import threading
import time

ENT = "llm"
METHOD = "call"
COMMAND = "llm::call {{about}} {{prompt}}"
CALLER_PER_HOUR = 12
GLOBAL_PER_HOUR = 120
PROMPT_MAX = 4000
RING = 20

_lock = threading.Lock()
_calls: list[tuple[float, str]] = []          # (ts, caller) of the last hour
_SEAT = {"srv": None}


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_COMPONENT, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_COMPONENT, properties={
            "name": "llm",
            "summary": ("THE MODEL, as an actuator: call(about, prompt) wakes a model turn (a loop with "
                        "the listener's hands) on the entity `about` with that prompt; it returns at once and "
                        "the verdict lands on `about`. Capped per caller and per hour -- every call is "
                        "counted (`calls`, `state`). Call it when the field can't do the work itself.")})
        est.docs[ent.eid] = ent
    return ent


def bind(srv) -> None:
    """Boot: the entity and its method."""
    _SEAT["srv"] = srv
    try:
        est = getattr(srv, "entity_store", None)
        ent = _entity(srv)
        if est is None or ent is None:
            return
        def _of(d):
            mo = (d.properties or {}).get("method_of")
            return ent.eid in (mo if isinstance(mo, list) else [mo])
        if not any(_of(d) and (d.properties or {}).get("method_name") == METHOD
                   for d in list((est.docs or {}).values())):
            from .night import method_define
            method_define(srv, est, None, None, on=ent.eid, method=METHOD, command=COMMAND)
        est.upsert(ent, embedder=None)
    except Exception as e:                      # noqa: BLE001
        import sys
        print(f"[llm] the llm entity not wired ({e!r})", file=sys.stderr)


def seat():
    return _SEAT["srv"]


def _budget(caller: str, now: float) -> str | None:
    with _lock:
        _calls[:] = [c for c in _calls if now - c[0] < 3600]
        if len(_calls) >= GLOBAL_PER_HOUR:
            return f"the hour's budget is spent ({GLOBAL_PER_HOUR} calls)"
        if sum(1 for _, c in _calls if c == caller) >= CALLER_PER_HOUR:
            return f"{caller} has called {CALLER_PER_HOUR} times this hour"
        _calls.append((now, caller))
    return None


def request(srv, prompt: str, about: str = "", caller: str = "", now: float | None = None) -> dict:
    """Queue a model turn on `about` with `prompt`: {rc, text}."""
    now = now or time.time()
    prompt = str(prompt or "").strip()[:PROMPT_MAX]
    caller = str(caller or "unknown")[:120]
    if srv is None:
        return {"rc": 127, "text": "no seat"}
    if not prompt:
        return {"rc": 2, "text": "a call needs a prompt (what the model should do)"}
    try:
        from .opencode import nerve_paused
        if nerve_paused(srv):
            return {"rc": 1, "text": "the seat is paused: no model calls until the operator resumes it"}
    except Exception:
        pass
    pool = getattr(srv, "events_pool", None)
    if pool is None:
        return {"rc": 127, "text": "no loops on this seat"}
    est = getattr(srv, "entity_store", None)
    target = None
    if about and est is not None:
        try:
            target = est._resolve_spec(str(about))
        except Exception:
            target = None
    ent = _entity(srv)
    about_eid = target.eid if target is not None else (ent.eid if ent is not None else "")
    why = _budget(caller, now)
    if why:
        _remember(srv, ent, now, caller, about_eid, prompt, refused=why)
        return {"rc": 1, "text": f"not called: {why}"}
    pool.put({"kind": "llm-call", "entity": about_eid, "prompt": prompt, "caller": caller, "ts": now,
              "event_id": f"llm-{int(now * 1000)}"})
    _remember(srv, ent, now, caller, about_eid, prompt)
    try:
        from . import score as _SC
        _SC.note(f"call:{caller.split(':', 1)[0]}")
    except Exception:
        pass
    return {"rc": 0, "text": f"queued: a loop takes it up on {about_eid}; its verdict lands there "
                             "(watch_last_decision)"}


def _remember(srv, ent, now, caller, about, prompt, refused: str = "") -> None:
    if ent is None:
        return
    pp = ent.properties or {}
    item = {"ts": round(now, 1), "caller": caller, "about": about, "prompt": prompt[:160]}
    if refused:
        item["refused"] = refused
    pp["calls"] = (list(pp.get("calls") or []) + [item])[-RING:]
    with _lock:
        n = len(_calls)
    pp["state"] = f"{n} call(s) in the last hour (cap {GLOBAL_PER_HOUR}; {CALLER_PER_HOUR} per caller)"
    ent.properties = pp


def from_command(rest: str, caller: str = "method") -> dict:
    """`llm::call <about> <prompt>` (the method's command, shell-quoted)."""
    try:
        parts = shlex.split(rest)
    except ValueError:
        parts = rest.split(None, 1)
    if len(parts) < 2:
        return {"rc": 2, "text": "call(about, prompt): name the entity it is about and what to do"}
    about, prompt = parts[0], " ".join(parts[1:])
    return request(seat(), prompt, about=about, caller=caller)
