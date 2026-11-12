"""#1120 (operator 2026-11-11, "using getters?"): a worker's reading round is judged by what its calls DID. A call of
the field's own families that wrote nothing in the field only read (a getter evaluated, an entity looked at); a
method call reads when the field marks its method read_only; a shell run acts; the read-only set
(PARALLEL_READS) stays the fallback for the rest. RED first."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag_requests as DR  # noqa: E402
from membrane import server as MS  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

A = "membrane::task:x:a"


def _srv():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    est.upsert(QuantumEntity(eid="ha:light.port", cls="Component", properties={"name": "port light"}), embedder=None)
    for name, ro in (("level", True), ("turn_on", False)):
        est.upsert(QuantumEntity(eid=f"membrane::method:{name}", cls="Component", properties={
            "name": name, "method_name": name, "command": "echo x", "method_of": ["ha:light.port"],
            "read_only": ro}), embedder=None)
    return SimpleNamespace(entity_store=est)


def test_a_call_is_judged_by_what_it_did():
    srv = _srv()
    assert MS.call_reads(srv, "entity_read", {"spec": "a"}, wrote=True)                  # the read-only set
    assert MS.call_reads(srv, "entity_edit", {"spec": "a"}, wrote=False)                 # wrote nothing: a read
    assert not MS.call_reads(srv, "entity_edit", {"spec": "a"}, wrote=True)
    assert not MS.call_reads(srv, "method_call", {"object": "ha:light.port", "method": "level"}, wrote=False)   # #1101: no such tool
    assert not MS.call_reads(srv, "code_run", {"code": "cat x"}, wrote=False)            # a shell: outside the field
    assert not MS.call_reads(srv, "learn", {"lesson": "x"}, wrote=False)                 # not a field family


def test_the_writes_of_a_watched_chain_are_counted_and_the_machinerys_are_not():
    with W.source("worker", "a") as ch:
        W.watch_chain(ch["id"])
        try:
            with W.as_writer("llm"):
                W.emit("x.y", 1, 2)
            with W.as_writer("adapter:tasks"):
                W.emit("x.y", 2, 3)
            with W.as_writer("trigger:field:ready"):
                W.emit("x.y", 3, 4)
            assert W.chain_writes(ch["id"]) == 1
        finally:
            W.watch_chain(ch["id"], False)
    with W.source("other", "b"), W.as_writer("llm"):
        W.emit("x.y", 1, 2)
    assert W.chain_writes(ch["id"]) == 0


# ---- the local path ----------------------------------------------------------------------------------------------
def _call(cid, name, args="{}"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": args}}


def _turn(*msgs, writes=(), execute=None):
    """A worker turn; the executor writes in the field for the call ids in `writes` (or runs `execute`)."""
    if execute is not None:
        ran = []

        def _ex(name, args, cid=None, _f=execute):
            ran.append(cid)
            return _f(name, args, cid)
        return _turn_with(msgs, _ex, ran)
    ran = []

    def execute(name, args, cid=None):
        ran.append(cid)
        if cid in writes:
            with W.as_writer("llm"):
                W.emit("ha:light.port.level", 1, 2)
        return "ok"
    return _turn_with(msgs, execute, ran)


def _turn_with(msgs, execute, ran):
    state = {"n": 0}

    def ladder(body):
        raise AssertionError("streamed lane only")

    def stream(body, on_event):
        msg = msgs[min(state["n"], len(msgs) - 1)]
        state["n"] += 1
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}
    ladder.stream = stream
    tools = [{"name": n, "description": "", "inputSchema": {"type": "object"}}
             for n in ("entity_read", "entity_edit", "learn")]
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder, execute, system="", model="m",
                      tools=tools)
    r.srv = _srv()
    sid = r.sessions.create(title="w")["id"]
    r.sessions.sessions[sid]["agent"] = "worker: " + A
    out = r.run(sid, "go")
    return out, ran


def _capped(out):
    return isinstance((out.get("info") or {}).get(CA.CAP_MARK), dict)


RO = '{"spec": "ha:light.port"}'


def test_a_read_is_a_reading_round():
    out, ran = _turn({"tool_calls": [_call("r1", "entity_read", RO)]}, {"tool_calls": [_call("w1", "learn")]},
                     {"content": "DONE"})
    assert ran == ["r1", "w1"] and not _capped(out)


def test_an_edit_that_wrote_is_an_acting_round():
    out, ran = _turn({"tool_calls": [_call("e1", "entity_edit", '{"spec": "x"}')]},
                     {"tool_calls": [_call("w1", "learn")]}, {"content": "DONE"}, writes=("e1",))
    assert ran == ["e1"] and _capped(out)


def test_an_edit_that_changed_nothing_only_read():
    out, ran = _turn({"tool_calls": [_call("e1", "entity_edit", '{"spec": "x"}')]},
                     {"tool_calls": [_call("w1", "learn")]}, {"content": "DONE"})
    assert ran == ["e1", "w1"] and not _capped(out)


# ---- the Claude rung -----------------------------------------------------------------------------------------------
def _answer():
    with mock.patch.object(DR, "current", lambda: {"lane": "worker"}):
        return CA._Answer()


def test_claude_a_step_that_wrote_is_an_acting_step():
    a = _answer()
    a.saw_step("m1", reads=True)
    a.call_done("entity_edit", {"spec": "x"}, wrote=True)
    a.saw_step("m2", reads=True)
    assert asyncio.run(a.hook({"tool_use_id": "t2"})).get("continue_") is False


def test_claude_a_step_that_wrote_nothing_only_read():
    a = _answer()
    a.saw_step("m1", reads=True)
    a.call_done("entity_edit", {"spec": "x"}, wrote=False)
    a.saw_step("m2", reads=True)
    assert asyncio.run(a.hook({"tool_use_id": "t2"})) == {}


# ---- operator 2026-11-11 (B): a method call's own bookkeeping is no write -----------------------------------------








def test_the_chain_keeps_what_was_written():
    with W.source("worker", "a") as ch:
        W.watch_chain(ch["id"])
        try:
            with W.as_writer("llm"):
                W.emit("ha:light.port.state", 1, 2, eid="ha:light.port")
            assert W.chain_written(ch["id"]) == [("ha:light.port", "state", "ha:light.port.state")]
        finally:
            W.watch_chain(ch["id"], False)




def test_local_chat_models_keep_their_rounds():
    assert MS.MAX_ROUNDS == 24                    # operator 2026-11-11: no 12-step ceiling for local chat models
