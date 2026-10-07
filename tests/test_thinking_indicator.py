"""THE THINKING INDICATOR (the operator's ask, the reuse-the-TUI's-
own-spinner shape): the reasoning part opencode's TUI already
understands — opened EMPTY ({time.start} only, no end) the TUI pins
its Thought block on a Thinking spinner; closed with time.end it
releases. THE COVENANT: the same indicator opens when REAL thinking
happens and closes when it's over — never while idle:

1. a turn OPENS a reasoning part right after step-start (the first
   round); when the model answers WITH reasoning: the same part gets
   the delta + the close (no double-open);
2. a tool round: the part closes BEFORE the tool runs; the NEXT
   round reopens it at step-start;
3. a turn's end: NO part left open (the spinner always releases);
4. the event-thinkers' inject: opens its own reasoning part, streams
   the line as the delta, closes — a pulse;
5. an idle session (no turn, no thinkers) emits NOTHING.
"""
import threading
import time

import pytest

from membrane.server import TurnRunner, SSEHub, SessionStore, MessageStore


def _resp(reasoning=None, content=None, calls=None):
    m = {"content": content}
    if reasoning is not None:
        m["reasoning"] = reasoning
    if calls:
        m["tool_calls"] = calls
    return {"choices": [{"message": m, "finish_reason":
                         "tool_calls" if calls else "stop"}]}


def _stub_ladder(responses, calls):
    def call(body):
        calls.append(body)
        r = responses[len(calls) - 1]
        if isinstance(r, Exception):
            raise r
        return r
    return call


def _world(responses, tools=None):
    hub = SSEHub()
    sessions = SessionStore()
    msgs = MessageStore()
    calls, exec_calls = [], []
    ladder = _stub_ladder(responses, calls)
    executor = lambda name, args, call_id: (
        exec_calls.append((name, args)) or "ok\n")  # noqa: E731
    s = sessions.create()
    tools_arg = [{"type": "function", "function": {"name": "echo",
                 "description": "echo", "parameters":
                 {"type": "object", "properties": {}}}}] if tools else None
    t = TurnRunner(hub, sessions, msgs, ladder, executor,
                   system="SYS", model="glm-x", tools=tools_arg)
    return dict(hub=hub, sessions=sessions, msgs=msgs, calls=calls,
                ec=exec_calls, s=s, t=t, tools_arg=tools_arg, tools=tools)


def _types(hub, q):
    out = []
    try:
        while True:
            e = q.get(timeout=0.3)
            out.append(e)
    except Exception:
        pass
    return out


TC = {"type": "function",
      "function": {"name": "echo", "description": "echo it",
                   "parameters": {"type": "object",
                                  "properties": {"x": {"type": "string"}},
                                  "required": ["x"]}}}


def test_turn_opens_and_closes_the_thinking_part():
    """A turn with model-provided reasoning: ONE reasoning part opens
    at step-start, the model's text rides as the delta, it closes —
    no double-open."""
    w = _world([_resp(reasoning="I chewed it", content="pong")])
    q = w["hub"].subscribe()
    w["t"].run(w["s"]["id"], "say pong")
    events = _types(w["hub"], q)
    types = [e["type"] for e in events]
    # the reasoning parts:
    rparts = [e.get("part") for e in events
              if e["type"] == "message.part.updated"
              and isinstance(e.get("part"), dict)
              and e["part"].get("type") == "reasoning"]
    assert rparts, types
    assert len(rparts) == 1, rparts            # ONE, not double-open
    p = rparts[0]
    assert p["text"] == "I chewed it"
    assert p["time"]["start"] and p["time"]["end"]  # closed


def test_a_no_reasoning_turn_still_indicates_then_releases():
    """The model answered with NO reasoning: the open part's honest
    close (it thought briefly, nothing said) — the TUI's spinner
    doesn't hang past the turn."""
    w = _world([_resp(content="pong")])
    q = w["hub"].subscribe()
    w["t"].run(w["s"]["id"], "say pong")
    events = _types(w["hub"], q)
    rparts = [e.get("part") for e in events
              if e["type"] == "message.part.updated"
              and isinstance(e.get("part"), dict)
              and e["part"].get("type") == "reasoning"]
    if rparts:
        p = rparts[-1]
        assert p["time"].get("end"), p     # always closed


def test_tool_round_closes_and_reopens():
    """A tool round: the thinking part closes BEFORE the tool part
    streams; the second round REOPENS its own; both close."""
    w = _world([_resp(calls=[{"id": "c1", "function": {
                                "name": "echo",
                                "arguments": "{\"x\": \"1\"}"}}]),
                _resp(content="done")],
               tools=True)
    q = w["hub"].subscribe()
    w["t"].run(w["s"]["id"], "echo it")
    events = _types(w["hub"], q)
    order = [(e["type"], (e.get("part") or {}).get("type")
              if isinstance(e.get("part"), dict) else None)
             for e in events if e["type"].startswith("message.part")]
    # the reasoning opens appear TWICE (one per round):
    rparts = [e.get("part") for e in events
              if e["type"] == "message.part.updated"
              and isinstance(e.get("part"), dict)
              and e["part"].get("type") == "reasoning"]
    assert len(rparts) == 2, order
    # both closed:
    assert all(p["time"].get("end") for p in rparts), rparts
    # the tool part rode BETWEEN the closes:
    tool_ix = [i for i, (t, pt) in enumerate(order) if pt == "tool"]
    assert tool_ix, order


def test_no_open_parts_at_the_turns_end():
    """The turn's parts all close — a spinner hangs never."""
    w = _world([_resp(reasoning="deep", content="hi")])
    w["t"].run(w["s"]["id"], "say hi")
    out = w["t"].run(w["s"]["id"], "again")
    for p in out["parts"]:
        t = p.get("time") or {}
        if p.get("type") in ("reasoning", "text"):
            assert t.get("end"), p


def test_idle_session_emits_nothing():
    """An idle session (no turn): the hub hears NOTHING — no spinner
    ever while idle (the cosmetic-busy's honesty)."""
    w = _world([_resp(content="pong")])
    q = w["hub"].subscribe()
    time.sleep(1.2)                        # over the heartbeat + a beat
    events = _types(w["hub"], q)
    # only the boot's own server.connected (if any):
    assert not [e for e in events if e["type"] != "server.connected"], events