"""#1120 (operator 2026-11-11, live on #1111: split chains six deep on small chores): "read, then act" is ONE leaf.
A leaf may take one READING round (every call read-only) and then one acting round, then its verdict; a second
acting round still ends it split. Read-only = the seat's own read-only marking, server.PARALLEL_READS (the calls
safe to run together), after toolset.resolve. RED first."""
import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag_requests as DR  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import server as MS  # noqa: E402

A = "membrane::task:x:a"


def test_reads_are_the_seats_read_only_calls():
    assert MS.reads_only([("find", {"action": "query", "q": "x"}), ("entity_read", {"spec": "a"})])
    assert MS.reads_only([("mcp__membrane__query", {"q": "x"})])           # the Claude rung's names too
    assert not MS.reads_only([("find", {}), ("entity_edit", {"spec": "a"})])
    assert not MS.reads_only([("exec", {"cmd": "cat x"})])                 # exec isn't marked read-only
    assert not MS.reads_only([])


# ---- the local path (TurnRunner rounds) ------------------------------------------------------------------------
def _call(cid, name):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _turn(agent, *msgs):
    state = {"n": 0}

    def ladder(body):
        raise AssertionError("streamed lane only")

    def stream(body, on_event):
        msg = msgs[min(state["n"], len(msgs) - 1)]
        state["n"] += 1
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}
    ladder.stream = stream
    ran = []
    tools = [{"name": n, "description": "", "inputSchema": {"type": "object"}} for n in ("find", "query", "learn")]
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                      lambda name, args, cid=None: ran.append(cid) or "ok", system="", model="m", tools=tools)
    sid = r.sessions.create(title=agent)["id"]
    r.sessions.sessions[sid]["agent"] = agent
    out = r.run(sid, "go")
    return out, ran


def _capped(out):
    return isinstance((out.get("info") or {}).get(CA.CAP_MARK), dict)


def test_a_local_leaf_reads_then_acts_in_one_leaf():
    out, ran = _turn("worker: " + A, {"tool_calls": [_call("r1", "query"), _call("r2", "query")]},
                     {"tool_calls": [_call("w1", "learn")]}, {"content": "DONE it"})
    assert ran == ["r1", "r2", "w1"] and not _capped(out)


def test_a_second_acting_round_still_splits():
    out, ran = _turn("worker: " + A, {"tool_calls": [_call("r1", "query")]}, {"tool_calls": [_call("w1", "learn")]},
                     {"tool_calls": [_call("w2", "learn")]}, {"content": "DONE"})
    assert ran == ["r1", "w1"] and _capped(out)


def test_act_first_then_anything_splits():
    out, ran = _turn("worker: " + A, {"tool_calls": [_call("w1", "learn")]}, {"tool_calls": [_call("r1", "query")]},
                     {"content": "DONE"})
    assert ran == ["w1"] and _capped(out)


def test_only_one_reading_round():
    out, ran = _turn("worker: " + A, {"tool_calls": [_call("r1", "query")]}, {"tool_calls": [_call("r2", "query")]},
                     {"tool_calls": [_call("w1", "learn")]}, {"content": "DONE"})
    assert ran == ["r1", "r2"] and _capped(out)


# ---- the Claude rung (the step tracker) -------------------------------------------------------------------------
def _answer():
    with mock.patch.object(DR, "current", lambda: {"lane": "worker"}):
        return CA._Answer()


def _hook(a, tid):
    return asyncio.run(a.hook({"tool_use_id": tid}))


def test_claude_a_reading_step_then_an_acting_step_is_one_leaf():
    a = _answer()
    a.saw_step("m1", reads=True)
    assert _hook(a, "t1") == {}
    a.saw_step("m2", reads=False)
    assert _hook(a, "t2") == {}
    a.saw_step("m3", reads=True)
    assert _hook(a, "t3").get("continue_") is False


def test_claude_an_acting_first_step_allows_no_second():
    a = _answer()
    a.saw_step("m1", reads=False)
    a.saw_step("m2", reads=True)
    assert _hook(a, "t2").get("continue_") is False


def test_claude_a_step_is_read_only_only_if_all_its_blocks_are():
    a = _answer()
    a.saw_step("m1", reads=True)
    a.saw_step("m1", reads=False)                       # the same step's second block writes
    a.saw_step("m2", reads=True)
    assert _hook(a, "t2").get("continue_") is False


def test_claude_reads_of_a_message():
    class B:
        def __init__(self, name, inp):
            self.name, self.input = name, inp
    B.__name__ = "ToolUseBlock"
    assert CA.step_reads([B("mcp__membrane__find", {"action": "query", "q": "x"})]) is True
    assert CA.step_reads([B("mcp__membrane__find", {}), B("mcp__membrane__learn", {})]) is False


def test_the_texts_say_read_then_act():
    assert "read" in GG.WORKER_TEXT.lower() and "then" in GG.WORKER_TEXT.lower()
    assert "reading round" in CA.STEP_MESSAGE.lower() or "read" in CA.STEP_MESSAGE.lower()
    text = (Path(GG.__file__).resolve().parent / "help" / "goals.md").read_text().lower()
    assert "read" in text and "acting" in text
