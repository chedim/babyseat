"""TOOLS THAT DON'T BLOCK (2026-10-12, the operator: "can we make all tool calls
that run scripts asynchronous? theyre blocking the fun stuff too much ... maybe
even entity reads? like, it just drops into chat when its ready ... finishing
such a task should be treated almost like an event").

  reads    entity read returns the STORED values at once (with their age); the
           getter refreshes in the background, and fresh values that differ
           come back as a job result
  runs     exec link / call / each, entity read run=true: a result within
           DEADLINE_S returns inline, as before; a slower one returns
           "job #N running -- NOT finished" and its result comes back later
  defines  scripts set by entity add / edit return at once (the syntax is
           checked inline by the store); the first fire runs in the
           background and its outcome comes back later

A FINISHED JOB IS AN EVENT: back to the chat session that started it as a
continuation turn ("[membrane] job #N finished ..." -- visible, Argus answers;
mid-turn it joins the next round like a queued message); a loop's job wakes a
loop pass on the loop's own entity (kind 'job-done'). Depth: a job started inside a
job-woken turn of depth >= MAX_DEPTH wakes nothing (no read -> reply -> read
chains); its result still lands on the entity and in the field.
"""
from __future__ import annotations

import threading
import time

DEADLINE_S = 3.0
MAX_DEPTH = 2
RESULT_MAX = 1500
RUN_TOOLS = frozenset(("entity_link", "method_call", "method_each"))
READ_TOOL = "entity_read"
DEFINE_TOOLS = frozenset(("entity_edit", "entity_add"))
_SCRIPT_KEYS = ("getter_script", "tick_script", "watch_script", "mention_script", "thought_script",
                "dreamed_script", "nudge_script", "delete_script")

_lock = threading.Lock()
_N = {"n": 0}
DEPTH: dict[str, int] = {}          # session -> the job depth of the turn running in it
PENDING_DEPTH: dict[str, int] = {}  # a loop session about to run a job-done pass -> its depth


def _next() -> int:
    with _lock:
        _N["n"] += 1
        return _N["n"]


def _truthy(v) -> bool:
    return v is True or str(v).lower() in ("1", "true", "yes")


def _spec(args: dict) -> str:
    for k in ("spec", "eid", "entity", "name", "key", "id", "object"):
        v = (args or {}).get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _is_loop(runner, sid: str) -> bool:
    try:
        from .server import _lane_of
        agent = (getattr(runner, "_agents", None) or {}).get(sid)
        return _lane_of(agent) in ("listener", "plan") and str(agent or "") not in ("", "build", "plan")
    except Exception:
        return False


def dispatch(runner, sid: str, name: str, args: dict, call_id: str, call) -> str:
    """Run one tool call under the job rules; `call(args)` runs it for real."""
    if name == READ_TOOL and not _truthy((args or {}).get("run")):
        return _read(runner, sid, args, call)
    if name in RUN_TOOLS or (name == READ_TOOL and _truthy((args or {}).get("run"))):
        return _run(runner, sid, name, args, call)
    if name in DEFINE_TOOLS and any((args or {}).get(k) for k in _SCRIPT_KEYS):
        return _define(runner, sid, name, args, call)
    return call(args)


# ---- runs: inline within the deadline, a job past it ----------------------------------
def _run(runner, sid, name, args, call) -> str:
    box: dict = {}
    done = threading.Event()
    t0 = time.time()

    def go():
        try:
            box["out"] = call(args)
        except Exception as e:                    # noqa: BLE001
            box["out"] = f"Error: {type(e).__name__}: {e}"[:400]
        done.set()
    threading.Thread(target=go, daemon=True, name=f"tool-{name}").start()
    if done.wait(DEADLINE_S):
        return box.get("out") or ""
    n = _next()
    what = _what(name, args)
    depth = int(DEPTH.get(sid) or 0)

    def finish():
        done.wait()
        deliver(runner, sid, n, what, str(box.get("out") or ""), round(time.time() - t0, 1), depth,
                entity=_spec(args))
    threading.Thread(target=finish, daemon=True, name=f"job-{n}").start()
    return (f"⏳ job #{n} is still running ({what}) -- it is NOT finished. Don't report its result: "
            f"it arrives as an event, a '[membrane] job #{n} finished' message. Go on with other work "
            f"or end your turn.")


# ---- reads: stored values now, the refresh as a job ----------------------------------
def _read(runner, sid, args, call) -> str:
    out = call({**(args or {}), "nofire": True})
    srv = getattr(runner, "srv", None)
    est = getattr(srv, "entity_store", None)
    ent = None
    try:
        ent = est.resolve(_spec(args)) if est is not None else None
    except Exception:
        ent = None
    if ent is None:
        return out
    from .night import effective_script
    if not effective_script(est, ent, "getter")[0]:
        return out
    pp = ent.properties or {}
    ts = float(pp.get("_last_reading_ts") or pp.get("state_ts") or 0)
    age = f"as of {int(time.time() - ts)} s ago" if ts else "never refreshed yet"
    before = dict(pp.get("script_data") or {})
    n = _next()
    depth = int(DEPTH.get(sid) or 0)
    t0 = time.time()

    def refresh():
        try:
            from .watch_agents import entity_read_back
            res = entity_read_back(srv, ent, by="model", notify=False)   # the job answers the reader (#188)
        except Exception as e:                    # noqa: BLE001
            res = {"error": repr(e)}
        after = dict((ent.properties or {}).get("script_data") or {})
        if res.get("error"):
            deliver(runner, sid, n, f"refresh of {ent.eid}", f"the getter failed: {res['error']}",
                    round(time.time() - t0, 1), depth, entity=ent.eid)
            return
        moved = {k: (before.get(k), v) for k, v in after.items() if before.get(k) != v}
        if moved:                                 # unchanged values are no news
            body = "; ".join(f"{k}: {a!s} -> {b!s}" for k, (a, b) in list(moved.items())[:12])
            deliver(runner, sid, n, f"fresh values of {ent.eid}", body, round(time.time() - t0, 1), depth,
                    entity=ent.eid)
    threading.Thread(target=refresh, daemon=True, name=f"job-{n}").start()
    return (f"{out}\n(these are the STORED values, {age}; the getter refreshes as job #{n} -- "
            "values that changed arrive as an event)")


# ---- defines: accepted now, the first fire as a job ----------------------------------
def _define(runner, sid, name, args, call) -> str:
    out = call({**(args or {}), "defer_post": True})
    if str(out).lstrip().lower().startswith(("entity not written", "error", "refused")) or " failed" in str(out)[:80]:
        return out
    srv = getattr(runner, "srv", None)
    est = getattr(srv, "entity_store", None)
    if est is None or not any((args or {}).get(k) for k in ("getter_script",)):
        return out                                # only a getter fires on the define
    n = _next()
    depth = int(DEPTH.get(sid) or 0)
    t0 = time.time()
    spec = _spec(args)

    def first_fire():
        time.sleep(0.2)
        try:
            ent = est.resolve(spec)
            from .watch_agents import entity_read_back
            res = entity_read_back(srv, ent, force=True, by="model", notify=False) \
                if ent is not None else {"error": "gone"}           # the job answers the definer (#188)
        except Exception as e:                    # noqa: BLE001
            res = {"error": repr(e)}
        if res.get("error"):
            body = f"the new getter FAILED its first fire: {res['error']}"
        else:
            st = res.get("state") or {}
            body = ("the new getter's first fire: " + ", ".join(f"{k}={str(v)[:40]}" for k, v in list(st.items())[:10])
                    if st else f"the new getter fired: {str(res.get('note') or 'no state returned')[:300]}")
        deliver(runner, sid, n, f"first fire of {spec}", body, round(time.time() - t0, 1), depth, entity=spec)
    threading.Thread(target=first_fire, daemon=True, name=f"job-{n}").start()
    return f"{out}\n(the getter's first fire runs as job #{n}; its outcome arrives as an event)"


def _what(name: str, args: dict) -> str:
    a = args or {}
    if name == "entity_link":
        c = a.get("commands")
        return f"exec link {str(c if not isinstance(c, list) else c[0])[:80]}"
    if name in ("method_call", "method_each"):
        return f"{_spec(a) or a.get('object', '?')}.{a.get('method', '?')}"
    return f"{name} {_spec(a)}"[:100]


# ---- a finished job is an event ------------------------------------------------------
def deliver(runner, sid: str, n: int, what: str, result: str, secs: float, depth: int,
            entity: str = "") -> str:
    """Back to whoever started it: the chat as a continuation, a loop as a
    pass on the entity; past MAX_DEPTH it wakes nothing."""
    result = str(result or "")[:RESULT_MAX]
    text = f"[membrane] job #{n} finished ({what}, {secs} s):\n{result}"
    if depth >= MAX_DEPTH:
        return "dropped: depth"
    return deliver_event(runner, sid, text, kind="job-done", entity=entity, tool="job",
                         key=f"job{n}", call_id=f"job-{n}", title=f"job #{n} finished · {what} ({secs} s)",
                         card_input={"job": n, "what": what, "seconds": secs}, output=result,
                         loop_extra={"job": n, "what": what, "result": result, "secs": secs,
                                     "job_depth": depth + 1, "event_id": f"job-{n}"},
                         turn_extra={"jobDepth": depth + 1})


def deliver_event(runner, sid: str, text: str, kind: str, entity: str = "", tool: str = "",
                  key: str = "", title: str = "", card_input: dict | None = None,
                  output: str = "", call_id: str = "", loop_extra: dict | None = None,
                  turn_extra: dict | None = None) -> str:
    """AN EVENT BACK TO ITS SESSION (a finished job; an answered Decision card,
    decision_map.answered): a loop session gets a pass on its own entity (an
    events-pool event of `kind`, the way a fired watch arrives); a chat gets a
    tool box in its transcript and a continuation turn carrying `text`.
    Returns 'loop', 'chat', or why nothing was delivered."""
    srv = getattr(runner, "srv", None)
    if _is_loop(runner, sid):
        pool = getattr(srv, "events_pool", None)
        if pool is None:
            return "no pool"
        # BACK TO THE READER'S OWN PASS (#188): a goal's loop read the eye and
        # the result woke a pass on the EYE -- the loop's agent names its
        # entity ('watch: <eid>'); the job's subject rides only as `what`
        agent = str((getattr(runner, "_agents", None) or {}).get(sid) or "")
        own = agent.split(": ", 1)[1].strip() if ": " in agent else ""
        pool.put({"kind": kind, "entity": own or entity, "text": text,
                  **(loop_extra or {}), "ts": time.time()})
        return "loop"
    sessions = getattr(runner, "sessions", None)
    row = sessions.get(sid) if sessions is not None else None
    if kind != "job-done" and not row:
        return "no session"              # a job always has its chat; an old card may not
    # THE RESULT IS A TOOL BOX, NOT A BUBBLE (2026-10-12, the operator: "NOT AS GIANT
    # BLUE BUBBLES ... put them back into tool call boxes"): a card row of its own,
    # one tool part (job #N, its output); the continuation turn takes the text
    # invisibly, as auto-chat's continuations do
    now = int(time.time() * 1000)
    tool = tool or kind
    key = key or f"{tool}{now}"
    agent = str((row or {}).get("agent") or "build")
    card = {"id": f"msg_{key}_{now}", "sessionID": sid, "role": "assistant", "parentID": "",
            "modelID": "membrane", "providerID": "membrane", "mode": "build", "agent": agent,
            "path": {"cwd": "", "root": ""}, "cost": 0.0,
            "tokens": {"total": 0, "input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
            "time": {"created": now, "completed": now}, "finish": "stop", "jobCard": True}
    part = {"id": f"prt_{key}_{now}", "sessionID": sid, "messageID": card["id"], "type": "tool",
            "callID": call_id or key, "tool": tool,
            "state": {"status": "completed", "input": dict(card_input or {}),
                      "output": output or text, "title": title or text[:120],
                      "time": {"start": now, "end": now}}}
    try:
        runner.messages.append(sid, card, [part])
        runner.hub.publish({"type": "message.updated", "sessionID": sid, "info": dict(card)})
        runner.hub.publish({"type": "message.part.updated", "part": dict(part), "time": now, "sessionID": sid})
    except Exception:
        pass
    info = {"id": f"msg_{key.replace(tool, tool + 'turn', 1)}_{now}", "sessionID": sid, "role": "user", "agent": agent,
            "continueTurn": True, "jobTurn": True, "jobDepth": 1, "jobText": text,
            "time": {"created": now}, **(turn_extra or {})}
    threading.Thread(target=lambda: runner.run(sid, "", user_info=info, user_parts=[]),
                     daemon=True, name=f"{key}-turn").start()
    return "chat"
