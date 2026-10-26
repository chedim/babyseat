"""#576 part E: effects are writes. A write of an adapter branch's property
(the chat thing's `say`, a toy's `poke`, the llm entity's `call` / `wake` /
`unwake`, presence's `restart`) runs the existing effect -- once per write,
never on an unrelated write, never on the adapter's own writes, on the
adapters' worker (the write returns first), in the write's chain."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as AD  # noqa: E402
from membrane import chat_io as CIO  # noqa: E402
from membrane import llm as LLM  # noqa: E402
from membrane import restarts as RS  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class _Srv:
    def __init__(self):
        self.entity_store = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _ent(est, eid, **props):
    est.upsert(QuantumEntity(eid=eid, cls="Component", properties=dict(props)))
    return est.docs[eid]


def _write(est, ent, prop, value, writer="field", chain=True):
    ent.properties[prop] = value
    if chain:
        with W.source("person", "op") as ch, W.as_writer(writer):
            est.upsert(ent)
        return ch["id"]
    with W.as_writer(writer):
        est.upsert(ent)
    return None


def _until(fn, timeout=3.0):
    t = time.time() + timeout
    while time.time() < t:
        if fn():
            return True
        time.sleep(0.01)
    return fn()


@pytest.fixture
def seat():
    srv = _Srv()
    AD.start(srv)
    yield srv
    AD.stop()


@pytest.fixture
def calls(monkeypatch):
    got = []

    def rec(name):
        def fn(*a, **k):
            ch = W.current()
            got.append({"fn": name, "a": a, "k": k, "chain": ch["id"] if ch else None,
                        "writer": W.writer(), "thread": threading.current_thread().name})
            return {"rc": 0, "text": f"{name} ok", "ok": True, "name": "x", "action": "x", "reading": {}}
        return fn
    monkeypatch.setattr(LLM, "request", rec("request"))
    monkeypatch.setattr(LLM, "set_wake", rec("set_wake"))
    monkeypatch.setattr(LLM, "unwake", rec("unwake"))
    monkeypatch.setattr(TB, "poke", rec("poke"))
    monkeypatch.setattr(RS, "do_restart", rec("do_restart"))
    # the chat thing's own path (on_edit) runs for real; only the session it posts into is faked
    monkeypatch.setattr(CIO, "_runner", lambda srv: object())
    monkeypatch.setattr(CIO, "latest_chat", lambda srv, person: "ses_1")
    monkeypatch.setattr(CIO, "_post", rec("post"))
    return got


def _settle(calls, n=1):
    assert _until(lambda: len(calls) >= n)
    AD.drain()
    time.sleep(0.05)


# ---- each branch fires its existing effect once per write ---------------------------

def test_a_chat_say_write_posts_the_line_once(seat, calls):
    est = seat.entity_store
    chat = _ent(est, CIO.eid_of(""), chat_io="chat", on=True, person="")
    cid = _write(est, chat, "say", "hello there")
    _settle(calls)
    assert [(c["fn"], c["a"][1:3]) for c in calls] == [("post", ("ses_1", "hello there"))]
    assert calls[0]["chain"] == cid
    assert chat.properties["say"] == ""                      # consumed: the next write is the next line
    assert chat.properties["said"][-1]["text"] == "hello there"


def test_a_toy_poke_write_pokes_the_toy_once(seat, calls):
    est = seat.entity_store
    die = _ent(est, MEMBRANE_NS + "Component:toy:die", toybox={"kind": "die", "name": "toy:die"})
    blk = _ent(est, MEMBRANE_NS + "Component:toy:tower:b3",
               toybox={"kind": "tower", "name": "toy:tower", "block": 3})
    drift = _ent(est, MEMBRANE_NS + "Component:toy:drift", toybox={"kind": "drift", "name": "toy:drift"})
    c1 = _write(est, die, "poke", "roll")
    c2 = _write(est, blk, "poke", "knock")
    c3 = _write(est, drift, "poke", "nudge up")
    _settle(calls, 3)
    got = sorted((c["a"][:3], c["k"].get("by"), c["chain"]) for c in calls)
    assert got == sorted([(("toy:die", "roll", None), "model", c1),
                          (("toy:tower", "knock", "3"), "model", c2),
                          (("toy:drift", "nudge", "up"), "model", c3)])
    assert die.properties["poke"] == "" and "poke_result" in die.properties


def test_a_persons_poke_write_pokes_as_that_person(seat, calls):
    est = seat.entity_store
    die = _ent(est, MEMBRANE_NS + "Component:toy:die", toybox={"kind": "die", "name": "toy:die"})
    _write(est, die, "poke", "roll", writer="person:alice")
    _settle(calls)
    assert calls[0]["k"].get("by") == "alice"              # the label of who rolled it


def test_an_llm_call_write_requests_a_turn_once(seat, calls):
    est = seat.entity_store
    llm = _ent(est, MEMBRANE_NS + "llm", name="llm")
    cid = _write(est, llm, "call", {"about": "lamp", "prompt": "is it on?"}, writer="llm:sonnet")
    _settle(calls)
    assert len(calls) == 1 and calls[0]["fn"] == "request"
    assert calls[0]["a"][1] == "is it on?" and calls[0]["k"]["about"] == "lamp"
    assert calls[0]["k"]["caller"] == "llm:sonnet"
    assert calls[0]["chain"] == cid
    assert llm.properties["call"] == "" and llm.properties["call_result"] == "request ok"


def test_an_llm_wake_and_unwake_write_set_and_cancel_wakes(seat, calls):
    est = seat.entity_store
    llm = _ent(est, MEMBRANE_NS + "llm", name="llm")
    cid = _write(est, llm, "wake", {"about": "lamp", "at": "in 2h", "prompt": "check it"})
    _settle(calls)
    _write(est, llm, "unwake", "w123")
    _settle(calls, 2)
    assert [c["fn"] for c in calls] == ["set_wake", "unwake"]
    assert calls[0]["a"][1:4] == ("lamp", "in 2h", "check it") and calls[0]["chain"] == cid
    assert calls[1]["a"][1] == "w123"


def test_a_presence_restart_write_restarts_the_seat_once(seat, calls):
    est = seat.entity_store
    pres = _ent(est, MEMBRANE_NS + "presence", seat="here")
    cid = _write(est, pres, "restart", "new code staged")
    _settle(calls)
    assert [(c["fn"], c["a"][1]) for c in calls] == [("do_restart", "new code staged")]
    assert calls[0]["chain"] == cid
    assert pres.properties["restart"] == ""


# ---- never on unrelated writes, never on its own -----------------------------------

def test_unrelated_writes_fire_nothing(seat, calls):
    est = seat.entity_store
    lamp = _ent(est, MEMBRANE_NS + "Component:lamp", name="lamp")
    _write(est, lamp, "say", "not a chat thing")             # a `say` on something else
    _write(est, lamp, "poke", "roll")                        # a `poke` on a non-toy
    _write(est, lamp, "call", {"about": "x", "prompt": "y"})  # a `call` that isn't the llm's
    _write(est, lamp, "restart", "why")                      # a `restart` off presence
    llm = _ent(est, MEMBRANE_NS + "llm", name="llm")
    _write(est, llm, "state", "3 calls")                     # the llm's other properties
    _write(est, llm, "call", "")                             # an emptied branch is no effect
    AD.drain()
    time.sleep(0.1)
    assert calls == []


def test_the_adapters_own_writes_fire_nothing(seat, calls):
    est = seat.entity_store
    llm = _ent(est, MEMBRANE_NS + "llm", name="llm")
    _write(est, llm, "call", {"about": "x", "prompt": "y"}, writer="adapter:llm")
    AD.drain()
    time.sleep(0.1)
    assert calls == []
    _write(est, llm, "call", {"about": "x", "prompt": "z"})
    _settle(calls)
    AD.drain()
    time.sleep(0.1)
    assert len(calls) == 1                                   # its consuming write did not fire again
    assert calls[0]["writer"] == "adapter:llm"


def test_a_birth_with_the_property_is_no_effect(seat, calls):
    est = seat.entity_store
    _ent(est, MEMBRANE_NS + "presence", restart="born with it")
    AD.drain()
    time.sleep(0.1)
    assert calls == []


# ---- outside the listener ------------------------------------------------------------

def test_the_effect_runs_after_the_write_returns(seat, monkeypatch):
    gate, ran = threading.Event(), []

    def slow(srv, why, *a, **k):
        gate.wait(5)
        ran.append((why, threading.current_thread().name))
        return {"rc": 0, "text": "ok"}
    monkeypatch.setattr(RS, "do_restart", slow)
    est = seat.entity_store
    pres = _ent(est, MEMBRANE_NS + "presence", seat="here")
    t0 = time.time()
    _write(est, pres, "restart", "go")
    assert time.time() - t0 < 1.0 and ran == []             # the write did not wait for the effect
    gate.set()
    assert _until(lambda: ran)
    assert ran[0][0] == "go" and ran[0][1] != threading.current_thread().name


def test_the_listener_never_writes(seat, calls, monkeypatch):
    """A listener must not write: the store sees only the writer's own write
    inside the call (the consuming write comes later, from the worker)."""
    seen = []
    W.on_write(seen.append)
    try:
        est = seat.entity_store
        pres = _ent(est, MEMBRANE_NS + "presence", seat="here")
        gate = threading.Event()
        monkeypatch.setattr(RS, "do_restart", lambda *a, **k: (gate.wait(5), {"rc": 0, "text": "ok"})[1])
        seen.clear()
        _write(est, pres, "restart", "go")
        assert [w["path"] for w in seen] == [pres.eid + ".restart"]
        gate.set()
    finally:
        W.off_write(seen.append)


def test_the_boot_starts_the_adapters():
    src = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
    assert "_AD.start(srv)" in src


def test_the_edit_route_and_the_adapter_post_a_line_once(seat, calls):
    """The edit route still posts a `say` at once (its receipt); the adapter
    then finds it consumed -- one line, whichever runs first."""
    est = seat.entity_store
    chat = _ent(est, CIO.eid_of(""), chat_io="chat", on=True, person="")
    for i in range(20):
        chat.properties["said_ts"] = 0
        _write(est, chat, "say", f"line {i}")
        CIO.on_edit(seat, chat)                              # what lanes' edit hook does
        AD.drain()
    assert [c["a"][2] for c in calls] == [f"line {i}" for i in range(20)]
