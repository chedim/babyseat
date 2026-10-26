"""#576 H (operator, 2026-10-26): Blurp never acts by itself while the LLM works.
A proposal (a guess that pays for itself) on a branch the operator ACCEPTED, firing
during an LLM turn, becomes a SUGGESTION in that conversation's next banner
(BLURP SUGGESTS); the LLM adopting it is a hit (refund), ignoring it drains it.
Unaccepted branches and shadow guesses stay silent."""
import os
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane import conv_banner as CB  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


@pytest.fixture
def seat(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False)
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    B.attach(srv)
    B.exchange(srv, {"awake": True, "mode": "on"})
    yield srv
    srv.ch_vm.detach()


def _toy(est, name):
    e = QuantumEntity(eid=MEMBRANE_NS + f"Component:toy:{name}", cls="toy", properties={"name": name})
    est.upsert(e)
    return est.docs[e.eid]


def _turn(srv, sid, *writes):
    """One LLM turn in session `sid`: the person's cause, then (maybe) the LLM's act."""
    with W.turn(sid):
        B.note_turn(W.current()["id"], "sid:" + sid)
        for ent, prop, val, who in writes:
            with W.as_writer(who):
                ent.properties[prop] = val
                srv.entity_store.upsert(ent)
    srv.ch_vm.drain()


def _teach(srv):
    est = srv.entity_store
    for n in "abc":                                  # define, hit, hit: a proposal
        e = _toy(est, n)
        _turn(srv, "s0", (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    cand = next(t for t in srv.ch_vm.triggers() if t.properties.get("write") == ["?x.b", "2"])
    assert srv.ch_vm.learner.state(cand.eid) == "proposal"
    return cand


def test_an_unaccepted_branch_never_suggests(seat):
    _teach(seat)
    d = _toy(seat.entity_store, "d")
    _turn(seat, "s1", (d, "a", 1, "person:op"))
    assert B.take_suggestions(seat, "sid:s1") == []
    assert "b" not in d.properties                   # and it never acts


def test_an_accepted_proposal_suggests_in_its_conversation_once(seat):
    _teach(seat)
    assert B.grant(seat, "Component:toy")
    d = _toy(seat.entity_store, "d")
    _turn(seat, "s1", (d, "a", 1, "person:op"))
    assert "b" not in d.properties                   # it suggests; it does not act
    got = B.take_suggestions(seat, "sid:s1")
    assert got == [f"set {d.eid}.b = 2"]
    assert B.take_suggestions(seat, "sid:s1") == []  # shown once
    assert B.take_suggestions(seat, "sid:other") == []


def test_adopting_refunds_and_ignoring_drains(seat):
    cand = _teach(seat)
    B.grant(seat, "Component:toy")
    lr = seat.ch_vm.learner
    b0 = lr.balance(cand.eid)
    d = _toy(seat.entity_store, "d")
    _turn(seat, "s1", (d, "a", 1, "person:op"), (d, "b", 2, "llm"))   # the LLM adopts
    assert lr.balance(cand.eid) > b0
    b1 = lr.balance(cand.eid)
    e = _toy(seat.entity_store, "e")
    _turn(seat, "s2", (e, "a", 1, "person:op"))                       # the LLM ignores
    assert lr.balance(cand.eid) < b1


def test_a_call_suggestion_reads_as_a_call(seat):
    line = B.suggestion_text(MEMBRANE_NS + "tool:toy.ping.invoke",
                             {"object": MEMBRANE_NS + "Component:toy:die", "args": {"n": 3}}, seat.entity_store)
    assert line.startswith("call ") and "toy:die" in line and "'n': 3" in line


def test_the_banner_carries_the_suggestions():
    out = CB.render({"suggest": ["set membrane::Component:toy:d.b = 2"]}, now=1_800_000_000)
    assert "BLURP SUGGESTS:" in out and "set membrane::Component:toy:d.b = 2" in out


def test_the_turn_records_its_conversation():
    import inspect
    from membrane import server
    assert "_CHB.note_turn(" in inspect.getsource(server.TurnRunner.run)
