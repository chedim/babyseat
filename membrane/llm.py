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

# SELF-SCHEDULED WAKES (2026-10-11, Argus: "I have no way to hold a goal and
# wake myself to pursue it"): wake(about, at, prompt) -- at that time the
# prompt becomes a model turn on `about`, through request() (caps, depth,
# the switch, pause and offline all apply). Kept on this entity (`wakes`):
# they survive restarts; one missed while the seat was down fires at boot,
# marked late. Time only: a CONDITION is a watch's job.
WAKE_METHOD = "wake"
WAKE_COMMAND = "llm::wake {{about}} {{at}} {{prompt}}"
UNWAKE_METHOD = "unwake"
UNWAKE_COMMAND = "llm::unwake {{id}}"
WAKE_MIN_S = 300.0
WAKE_MAX_S = 30 * 86400.0
WAKES_MAX = 20
WAKE_TICK_S = 30.0
DEFER_S = 600.0                  # a wake refused by the budget / the switch tries again this much later

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
        for meth, cmd in ((METHOD, COMMAND), (WAKE_METHOD, WAKE_COMMAND), (UNWAKE_METHOD, UNWAKE_COMMAND)):
            if not any(_of(d) and (d.properties or {}).get("method_name") == meth
                       for d in list((est.docs or {}).values())):
                from .night import method_define
                method_define(srv, est, None, None, on=ent.eid, method=meth, command=cmd)
        _paint_wakes(ent)
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


def request(srv, prompt: str, about: str = "", caller: str = "", now: float | None = None,
            extra: dict | None = None, readonly: bool | None = None) -> dict:
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
        try:
            fld = getattr(srv, "efield", None)
            if fld is not None:
                from . import streams as _ST
                fld.write_events([_ST.Event(stream="observation", tool="call",
                                            args={"object": "llm", "method": "call", "about": about_eid},
                                            result_head=f"llm.call by {caller} REFUSED: {why}",
                                            outcome="failed", ts=now, subject="m:llm")], session="calls")
        except Exception:
            pass
        return {"rc": 1, "text": f"not called: {why}"}
    with _lock:                                    # threads share the echo table (2026-10-11 sweep)
        _echo[key] = {"ts": now, "answer": None}
        if len(_echo) > 500:
            for k in sorted(list(_echo), key=lambda k: _echo[k]["ts"])[:250]:
                _echo.pop(k, None)
    field_call = caller.startswith("script:") if readonly is None else bool(readonly)
    pool.put({"kind": "llm-call", "entity": about_eid, "prompt": prompt, "caller": caller, "ts": now,
              "event_id": f"llm-{int(now * 1000)}", "depth": depth + 1, "echo": key,
              "origin": "field-call" if field_call else "call", "readonly": field_call, **(extra or {})})
    _remember(srv, ent, now, caller, about_eid, prompt)
    try:                                   # THE CALL LEAVES A TRACE IN THE FIELD (Argus: "calls and
        fld = getattr(srv, "efield", None)  # refusals leave no trace in the field at all")
        if fld is not None:
            from . import streams as _ST
            fld.write_events([_ST.Event(stream="observation", tool="call", args={"object": "llm", "method": "call",
                                                                                  "about": about_eid},
                                        result_head=f"llm.call by {caller} about {about_eid}: {prompt[:160]}",
                                        outcome="ok", ts=now, subject="m:llm")], session="calls")
    except Exception:
        pass
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
    with _lock:
        _e = _echo.get(ev.get("echo"))
        if _e is not None:
            _e["answer"] = line or "(no answer)"
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


# ---- self-scheduled wakes ------------------------------------------------------------
def parse_at(at: str, now: float) -> float:
    """'18:00' (its next occurrence), 'in 2h' / '3d' / '45m' / '1h30m', an ISO
    time, or epoch seconds -> the epoch it names. ValueError otherwise."""
    import re
    import datetime as _dt
    t = str(at or "").strip().lower()
    if not t:
        raise ValueError("when? '18:00', 'in 2h', '3d' or an ISO time")
    rel = re.fullmatch(r"(?:in\s+)?((?:\d+(?:\.\d+)?\s*(?:d|h|m|min|mins|minutes?|hours?|days?)\s*)+)", t)
    if rel:
        mult = {"d": 86400, "h": 3600, "m": 60}
        total = 0.0
        for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*([a-z]+)", rel.group(1)):
            total += float(n) * mult[u[0]]
        return now + total
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            raise ValueError(f"no such time: {at}")
        base = _dt.datetime.fromtimestamp(now)
        cand = base.replace(hour=h, minute=mi, second=0, microsecond=0)
        if cand.timestamp() <= now:
            cand += _dt.timedelta(days=1)
        return cand.timestamp()
    if re.fullmatch(r"\d{9,11}(?:\.\d+)?", t):
        return float(t)
    try:
        d = _dt.datetime.fromisoformat(str(at).strip().replace("Z", "+00:00"))
        return d.timestamp()
    except ValueError:
        raise ValueError(f"can't read the time '{at}': say '18:00', 'in 2h', '3d' or an ISO time")


def _hm(ts: float, now: float) -> str:
    fmt = "%H:%M" if abs(ts - now) < 86400 * 0.9 and time.localtime(ts).tm_yday == time.localtime(now).tm_yday \
        else "%a %m-%d %H:%M"
    return time.strftime(fmt, time.localtime(ts))


def _paint_wakes(ent, now: float | None = None) -> None:
    now = now or time.time()
    pp = ent.properties or {}
    ws = sorted(pp.get("wakes") or [], key=lambda w: float(w.get("at") or 0))
    pp["wakes"] = ws
    wl = (f"{len(ws)} wake(s) pending, next {_hm(float(ws[0]['at']), now)} on {ws[0].get('about')}"
          if ws else "no wakes pending")
    pp["wake_state"] = wl
    ent.properties = pp


def next_wake(srv, now: float | None = None) -> dict | None:
    ent = _entity(srv)
    ws = sorted(((ent.properties or {}).get("wakes") or []) if ent is not None else [],
                key=lambda w: float(w.get("at") or 0))
    return ws[0] if ws else None


def set_wake(srv, about: str, at: str, prompt: str, caller: str = "", now: float | None = None) -> dict:
    now = now or time.time()
    prompt = str(prompt or "").strip()[:PROMPT_MAX]
    if srv is None:
        return {"rc": 127, "text": "no seat"}
    if not prompt:
        return {"rc": 2, "text": "a wake needs a prompt (what to do when it fires)"}
    try:
        ts = parse_at(at, now)
    except ValueError as e:
        return {"rc": 2, "text": str(e)}
    if ts - now < WAKE_MIN_S:
        return {"rc": 2, "text": f"too soon: a wake is at least {int(WAKE_MIN_S // 60)} min out (sooner: call)"}
    if ts - now > WAKE_MAX_S:
        return {"rc": 2, "text": f"too far: at most {int(WAKE_MAX_S // 86400)} days out"}
    ent = _entity(srv)
    if ent is None:
        return {"rc": 127, "text": "no entity store"}
    est = getattr(srv, "entity_store", None)
    about_eid = ent.eid
    if about and est is not None:
        try:
            t = est._resolve_spec(str(about))
            about_eid = t.eid if t is not None else about_eid
        except Exception:
            pass
    with _lock:
        pp = ent.properties or {}
        ws = list(pp.get("wakes") or [])
        if len(ws) >= WAKES_MAX:
            return {"rc": 1, "text": f"{WAKES_MAX} wakes are pending already: unwake one first"}
        # THE SAME-MILLISECOND TWINS: the id is the wall clock's
        # millisecond; two wakes planted back to back minted the SAME
        # id (caught 2026-10-14) and one unwake cancelled BOTH -- the
        # pending id set disambiguates, every wake keeps its own.
        wid = f"w{int(now * 1000) % 10**9}"
        have = {str(w.get("id")) for w in ws}
        _n = 1
        while wid in have:
            wid = f"w{int(now * 1000) % 10**9}-{_n}"
            _n += 1
        w = {"id": wid, "about": about_eid, "at": round(ts, 1), "prompt": prompt,
             "caller": str(caller or "unknown")[:120], "set_ts": round(now, 1)}
        pp["wakes"] = ws + [w]
        ent.properties = pp
        _paint_wakes(ent, now)
    try:
        est.upsert(ent, embedder=None)
    except Exception:
        pass
    return {"rc": 0, "text": f"wake {w['id']} set for {_hm(ts, now)} on {about_eid} (unwake {w['id']} cancels it)"}


def unwake(srv, wid: str) -> dict:
    ent = _entity(srv)
    if ent is None:
        return {"rc": 127, "text": "no entity store"}
    wid = str(wid or "").strip()
    with _lock:
        pp = ent.properties or {}
        ws = list(pp.get("wakes") or [])
        keep = [w for w in ws if w.get("id") != wid]
        if len(keep) == len(ws):
            return {"rc": 1, "text": f"no pending wake {wid or '?'}" +
                    (f" (pending: {', '.join(w['id'] for w in ws)})" if ws else "")}
        pp["wakes"] = keep
        ent.properties = pp
        _paint_wakes(ent)
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass
    return {"rc": 0, "text": f"wake {wid} cancelled"}


def fire_due(srv, now: float | None = None, late_after_s: float = 120.0) -> list[dict]:
    """The due wakes, fired through request(); each leaves the list once fired."""
    now = now or time.time()
    ent = _entity(srv)
    if ent is None:
        return []
    with _lock:
        pp = ent.properties or {}
        ws = list(pp.get("wakes") or [])
        due = [w for w in ws if float(w.get("at") or 0) <= now]
        if not due:
            return []
        pp["wakes"] = [w for w in ws if w not in due]
        ent.properties = pp
    out = []
    for w in due:
        late = now - float(w["at"]) > late_after_s
        caller = str(w.get("caller") or "unknown")
        try:
            r = request(srv, w["prompt"], about=w.get("about") or "", caller="wake:" + caller, now=now,
                        readonly=caller.startswith("script:"),
                        extra={"wake": {"id": w["id"], "at": w["at"], "set_ts": w.get("set_ts"), "late": late}})
        except Exception as e:                       # noqa: BLE001 -- the wake waits, it isn't lost
            r = {"rc": 127, "text": f"error: {e!r}"[:200]}
        out.append({**w, "late": late, "rc": r["rc"], "text": r["text"]})

    # THE WAKE WAITS, IT ISN'T LOST (2026-10-11 sweep: a wake refused by the
    # hour's budget or the switch was dropped for good): no loops yet, paused,
    # an error -> back as it was; the budget spent or the model switched off
    # -> back, deferred DEFER_S (it says so when it fires)
    def _held(x):
        t = str(x["text"])
        return x["rc"] == 127 or "paused" in t
    def _deferred(x):
        t = str(x["text"])
        return x["rc"] == 1 and ("budget" in t or "this hour" in t or "switched off" in t)
    back = [{k: v for k, v in x.items() if k not in ("late", "rc", "text")} for x in out if _held(x)]
    for x in out:
        if _deferred(x):
            w2 = {k: v for k, v in x.items() if k not in ("late", "rc", "text")}
            w2["at"] = round(now + DEFER_S, 1)
            w2["deferred"] = int(w2.get("deferred") or 0) + 1
            w2["deferred_why"] = str(x["text"])[:120]
            back.append(w2)
    out = [x for x in out if not (_held(x) or _deferred(x))]
    with _lock:
        pp = ent.properties or {}
        if back:
            pp["wakes"] = list(pp.get("wakes") or []) + back
        pp["wakes_fired"] = (list(pp.get("wakes_fired") or []) +
                             [{"id": x["id"], "at": x["at"], "fired": round(now, 1), "about": x.get("about"),
                               "rc": x["rc"], "late": x["late"]} for x in out])[-RING:]
        ent.properties = pp
        _paint_wakes(ent, now)
    try:
        srv.entity_store.upsert(ent, embedder=None)
    except Exception:
        pass
    return out


def _decode_call_form(rest: str):
    """`{about: 'x', at: '18:00', prompt: 'y'}` -> dict or None (#17)."""
    import re
    m = re.fullmatch(
        r"\{\s*about:\s*['\"](?P<about>[^'\"]*)['\"]\s*,\s*"
        r"at:\s*['\"](?P<at>[^'\"]*)['\"]\s*,\s*"
        r"prompt:\s*['\"](?P<prompt>[^'\"]*)['\"]\s*,?\s*\}",
        rest.strip(), re.S)
    return m.groupdict() if m else None


def wake_from_command(rest: str, caller: str = "method") -> dict:
    """`llm::wake <about> <at> <prompt>` (shell-quoted)."""
    t = rest.strip()
    if t.startswith("{"):
        cf = _decode_call_form(t)
        if cf:
            return set_wake(seat(), cf["about"], cf["at"], cf["prompt"],
                            caller=caller)
        return {"rc": 2, "text": "wake: unrecognized call-form payload; "
                                 "use llm::wake <about> <at> <prompt>"}
    try:
        parts = shlex.split(t)
    except ValueError:
        parts = t.split(None, 2)
    if len(parts) < 3:
        return {"rc": 2, "text": "wake(about, at, prompt): the entity, when ('18:00', 'in 2h', '3d'), what to do"}
    return set_wake(seat(), parts[0], parts[1], " ".join(parts[2:]), caller=caller)


def unwake_from_command(rest: str) -> dict:
    try:
        parts = shlex.split(rest)
    except ValueError:
        parts = rest.split()
    return unwake(seat(), parts[0] if parts else "")


def start_wakes(srv) -> None:
    """The wakes' clock: every WAKE_TICK_S, on time (not only when idle)."""
    import os
    if os.environ.get("MEMBRANE_RUN", "1") == "0":
        return

    def loop():
        time.sleep(15)                         # the loops' pool comes up first
        while True:
            try:
                fire_due(srv)
            except Exception:
                pass
            time.sleep(WAKE_TICK_S)
    threading.Thread(target=loop, daemon=True, name="llm-wakes").start()
