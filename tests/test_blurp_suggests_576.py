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


# ---- #583 review -----------------------------------------------------------------------------

def test_a_suggestion_shown_in_one_turn_and_adopted_in_the_next_is_a_hit(seat):
    from membrane.capture import conversation_key
    cand = _teach(seat)
    B.grant(seat, "Component:toy")
    lr = seat.ch_vm.learner
    d = _toy(seat.entity_store, "d")
    _turn(seat, "s1", (d, "a", 1, "person:op"))                          # fires: a suggestion
    b0 = lr.balance(cand.eid)
    conv = conversation_key({"_turn_sid": "s1"})
    assert conv == "sid:s1"                                               # the banner's key is the turn's
    out = CB.render({"suggest": B.take_suggestions(seat, conv)}, now=1_800_000_000)
    assert f"set {d.eid}.b = 2" in out
    _turn(seat, "s1", (d, "b", 2, "llm"))                                 # the NEXT turn adopts it
    assert lr.balance(cand.eid) == pytest.approx(b0 + 2 * __import__("membrane.ch_learn").ch_learn.WRITE_COST)
    _turn(seat, "s1", (d, "b", 2, "llm"))                                 # adopting twice pays once
    assert lr.balance(cand.eid) == pytest.approx(b0 + 2 * __import__("membrane.ch_learn").ch_learn.WRITE_COST)


def test_a_long_value_is_a_preview_and_a_pointer():
    # the operator's form: a one-line preview, `->` where the whole value is read
    line = B.suggestion_text("membrane::Component:toy:d.note", "The meeting moved\nto Thursday " + "x" * 500,
                             cand="membrane::Trigger:cand-3f2a")
    assert line.startswith("set membrane::Component:toy:d.note = 'The meeting moved to Thursday ")
    assert line.endswith("…' -> membrane::Trigger:cand-3f2a.predicted.value")
    assert B.suggestion_text("membrane::Component:toy:d.b", "short") == "set membrane::Component:toy:d.b = 'short'"


def test_suggestions_outrank_the_rest_of_the_banner():
    long = ["x" * 200] * 3
    out = CB.render({"suggest": ["set a.b = 1"], "working": None, "alerts": long}, now=1_800_000_000)
    assert "set a.b = 1" in out
