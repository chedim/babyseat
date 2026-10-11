"""THE MODEL AS AN ENTITY (2026-10-11, the operator: "can we replace our
enforced synthetic llm calls with a more natural 'call-llm' entity?").

membrane::llm is an actuator like any other: its method `call(about,
prompt)` wakes a model turn -- a loop with the listener's hands -- on the
entity `about`, with that prompt as its task. Whoever decides the model is
needed calls it: Argus (exec(action='call', object='llm', method='call',
args={about, prompt})), a script (membrane.call_llm(prompt) -- one per
fire, like speak), and, step by step, the seat's own triggers (a watch's
reaction, auto-chat, the nudges) instead of composing prompts in code.

ARGUS'S FOUR EDGES (his review of this design, 2026-10-11):
  reentrancy  a call made inside a called turn carries its depth; past
              MAX_DEPTH the engine refuses (not etiquette)
  echo/cost   the same (about, prompt) within ECHO_S is not asked again --
              the earlier answer comes back; `enabled` on this entity is
              the switch (false: no calls at all)
  provenance  a SCRIPT's call is a field-call: its turn runs read-only
              (the plan lane: look, never write, never call again), tagged
              origin field-call
  output      a field-call answers in ONE line (VERDICT -- what it found),
              kept on the entity as llm_answer for the script's next fire

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

MAX_DEPTH = 2
ECHO_S = 600.0
_ctx = threading.local()                      # the depth of the called turn running in this thread
_echo: dict[str, dict] = {}                   # hash(about, prompt) -> {ts, answer}

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
    depth = int(getattr(_ctx, "depth", 0) or 0)
    if depth >= MAX_DEPTH:
        return {"rc": 1, "text": f"not called: a called turn may not call again past depth {MAX_DEPTH}"}
    ent0 = _entity(srv)
    if ent0 is not None and (ent0.properties or {}).get("enabled") is False:
        return {"rc": 1, "text": "not called: the model is switched off (llm.enabled = false)"}
    est = getattr(srv, "entity_store", None)
    target = None
    if about and est is not None:
        try:
            target = est._resolve_spec(str(about))
        except Exception:
            target = None
    ent = _entity(srv)
    about_eid = target.eid if target is not None else (ent.eid if ent is not None else "")
    import hashlib
    key = hashlib.sha1(f"{about_eid}\n{prompt}".encode()).hexdigest()[:16]
    seen = _echo.get(key)
    if seen and now - seen["ts"] < ECHO_S:
        ans = seen.get("answer")
        return {"rc": 0, "text": (f"already asked at {time.strftime('%H:%M', time.localtime(seen['ts']))}; "
                                  + (f"its answer: {ans}" if ans else "its answer is still coming"))}
    why = _budget(caller, now)
    if why:
        _remember(srv, ent, now, caller, about_eid, prompt, refused=why)
        return {"rc": 1, "text": f"not called: {why}"}
    _echo[key] = {"ts": now, "answer": None}
    if len(_echo) > 500:
        for k in sorted(_echo, key=lambda k: _echo[k]["ts"])[:250]:
            _echo.pop(k, None)
    field_call = caller.startswith("script:")
    pool.put({"kind": "llm-call", "entity": about_eid, "prompt": prompt, "caller": caller, "ts": now,
              "event_id": f"llm-{int(now * 1000)}", "depth": depth + 1, "echo": key,
              "origin": "field-call" if field_call else "call", "readonly": field_call})
    _remember(srv, ent, now, caller, about_eid, prompt)
    try:
        from . import score as _SC
        _SC.note(f"call:{caller.split(':', 1)[0]}")
    except Exception:
        pass
    try:
        from . import offline as _OFF
        if _OFF.is_offline():
            return {"rc": 0, "text": f"queued on {about_eid} -- the seat is OFFLINE (no model): it runs when "
                                     "the Mac is back"}
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


def answered(srv, ev: dict, text: str) -> None:
    """A called turn ended: its one line onto the entity (llm_answer) and the echo."""
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")[:300]
    if ev.get("echo") in _echo:
        _echo[ev["echo"]]["answer"] = line or "(no answer)"
    est = getattr(srv, "entity_store", None)
    doc = (est.docs or {}).get(str(ev.get("entity") or "")) if est is not None else None
    if doc is not None and line:
        pp = doc.properties or {}
        pp["llm_answer"] = {"ts": round(time.time(), 1), "text": line, "prompt": str(ev.get("prompt") or "")[:160],
                            "caller": ev.get("caller")}
        doc.properties = pp
        try:
            est.upsert(doc, embedder=None)
        except Exception:
            pass
