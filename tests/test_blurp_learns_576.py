"""#576 D2: Blurp anew -- the VM's learner attached to Blurp's body.

The seat's PURSE takes the learner's charges and refunds; Blurp's loop (its own
process, or in the seat) exchanges with it on every step through the context
call: it reports whether it is awake and takes the net energy, which it applies.
Nothing is spent until Blurp says it is awake; falling asleep prunes. A skill is
a BRANCH (a target's kind): open while guessing, earned when a guess there pays
for itself, and the operator's Accept grants it -- only then may a proposal act
there. The sheet shows the branches as the skill tree, the level as the branches
granted, and the Code tab as Blurp's triggers."""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane import ch_learn as L  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


@pytest.fixture
def seat(tmp_path):
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False)
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    B.attach(srv)
    yield srv
    srv.ch_vm.detach()


def _toy(est, name):
    e = QuantumEntity(eid=MEMBRANE_NS + f"Component:toy:{name}", cls="toy", properties={"name": name})
    est.upsert(e)
    return est.docs[e.eid]


def _turn(srv, *writes):
    with W.source("person", "t"):
        for ent, prop, val, who in writes:
            with W.as_writer(who):
                ent.properties[prop] = val
                srv.entity_store.upsert(ent)
    srv.ch_vm.drain()


def _cands(srv):
    return [t for t in srv.ch_vm.triggers() if (t.properties or {}).get("owner") == CH.SELF]


def test_the_branch_of_a_target():
    assert B.branch_of(MEMBRANE_NS + "Component:toy:die") == "Component:toy"
    assert B.branch_of(MEMBRANE_NS + "ha:light.port_strip") == "ha:light"
    assert B.branch_of(MEMBRANE_NS + "tool:any.run.invoke", {"object": MEMBRANE_NS + "Component:chat:op"}) \
        == "Component:chat"


def test_nothing_is_spent_until_blurp_says_it_is_awake(seat):
    x = _toy(seat.entity_store, "x")
    _turn(seat, (x, "a", 1, "person:op"), (x, "b", 2, "llm"))
    assert _cands(seat) == []                          # no report yet: the purse is closed
    out = B.exchange(seat, {"awake": True, "mode": "on"})
    assert out["energy_delta"] == 0
    _turn(seat, (x, "a", 3, "person:op"), (x, "b", 4, "llm"))
    assert _cands(seat)
    out = B.exchange(seat, {"awake": True, "mode": "on"})
    assert out["energy_delta"] < 0                     # the guesses were charged
    assert B.exchange(seat, {"awake": True, "mode": "on"})["energy_delta"] == 0   # taken once


def test_falling_asleep_prunes_the_negative(seat):
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})
    x, y = _toy(est, "x"), _toy(est, "y")
    _turn(seat, (x, "a", 1, "person:op"), (x, "b", 2, "llm"))
    _turn(seat, (y, "a", 1, "person:op"), (y, "b", 7, "llm"))     # the "= 2" guess was wrong on y
    bad = [t.eid for t in _cands(seat) if seat.ch_vm.learner.balance(t.eid) < 0]
    assert bad
    B.exchange(seat, {"awake": False, "asleep": True, "mode": "on"})
    assert not any(e in est.docs for e in bad)
    _turn(seat, (x, "a", 5, "person:op"), (x, "b", 6, "llm"))
    assert all(t.eid not in bad for t in _cands(seat))            # asleep: nothing new either


def test_accept_grants_a_branch_and_only_then_a_proposal_suggests(seat):
    from membrane import seat_panel as SP
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})
    a, b, c, d = (_toy(est, n) for n in "abcd")
    for e in (a, b, c):
        _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    cand = next(t for t in _cands(seat) if t.properties["write"] == ["?x.b", "2"])
    assert seat.ch_vm.learner.state(cand.eid) == "proposal"
    _turn(seat, (d, "a", 1, "person:op"), (d, "b", 2, "llm"))   # not granted: shadow, then the LLM confirms
    assert cand.properties["predicted"]["path"] == d.eid + ".b"
    assert seat.ch_vm.learner.state(cand.eid) == "proposal"
    SP.control(seat, {"accept": "branch:Component:toy"})
    assert B.granted(seat, d.eid + ".b")
    said = []
    seat.ch_vm.learner.suggest = lambda tr, path, value: said.append(path)
    e = _toy(est, "e")
    _turn(seat, (e, "a", 1, "person:op"))
    assert "b" not in e.properties and said == [e.eid + ".b"]     # granted: it suggests, never acts


def test_the_sheet_shows_branches_level_and_code(seat):
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})
    for n in "abc":
        e = _toy(est, n)
        _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    sheet = B.augment(seat, {"header": {"name": "Blurp", "level": 0}, "tree": [], "code": []})
    node = next(n for n in sheet["tree"] if n["id"] == "branch:Component:toy")
    assert node["state"] == "earned" and node["accepted"] is False
    assert sheet["level"] == 0 and sheet["header"]["level"] == 0
    assert any(c["state"] == "proposal" and c["line"].startswith("WHEN changed(?x.a)") for c in sheet["code"])
    from membrane import seat_panel as SP
    SP.control(seat, {"accept": "branch:Component:toy"})
    sheet = B.augment(seat, {"header": {"name": "Blurp", "level": 0}, "tree": [], "code": []})
    assert sheet["level"] == 1 and next(n for n in sheet["tree"] if n["id"] == "branch:Component:toy")["accepted"]


def test_blurps_step_reports_and_applies_its_energy(tmp_path, monkeypatch):
    from membrane import energy as E
    from membrane import self_loop as SL
    srv = types.SimpleNamespace(entity_store=EntityStore(path=str(tmp_path / "e.json"), shared=False),
                                _models_path=str(tmp_path / "m.json"), paused=False, efield=None)
    SL.bind(srv)
    SL.set_mode(srv, "on")
    seen = []
    monkeypatch.setattr(B, "exchange", lambda s, report: seen.append(report) or {"energy_delta": -0.25})
    e0 = E.level(srv)
    SL.step(srv)
    assert seen and seen[-1]["awake"] is True
    assert E.level(srv) == pytest.approx(e0 - 0.25, abs=0.01)


def test_the_seat_attaches_the_learner():
    import inspect
    from membrane import opencode
    assert "_CHB.attach(srv)" in inspect.getsource(opencode)


# ---- #580 review -----------------------------------------------------------------------------

def test_branches_are_kind_and_type():
    # operator: kind + type -- lights are not locks
    assert B.branch_of(MEMBRANE_NS + "ha:light.kitchen.b") == "ha:light"
    assert B.branch_of(MEMBRANE_NS + "ha:lock.front") == "ha:lock"
    assert B.branch_of(MEMBRANE_NS + "Component:toy:die.b") == "Component:toy"


def test_the_purse_closes_when_blurps_process_goes(seat):
    B.exchange(seat, {"awake": True, "mode": "on"})
    seat.ch_purse.charge(0.1)
    B.close_purse(seat)
    assert seat.ch_purse.awake is False and seat.ch_purse.delta < 0     # closed, the debt kept for next time
    import inspect
    from membrane import self_super
    assert "_CHB.close_purse(self.srv)" in inspect.getsource(self_super.Supervisor._exited)


def test_a_grant_is_live_only_once_saved(seat, tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_grants_file", lambda srv: tmp_path / "missing-dir" / "g.json")
    seat._ch_grants = None
    assert B.grant(seat, "ha:light") is False
    assert "ha:light" not in B.grants(seat)


def test_concurrent_grants_all_land(seat):
    import json
    import threading
    ts = [threading.Thread(target=B.grant, args=(seat, f"k:{i}")) for i in range(40)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    doc = json.loads(B._grants_file(seat).read_text())
    assert len({g["branch"] for g in doc["grants"]}) == 40


def test_a_step_that_falls_asleep_closes_the_purse(tmp_path, monkeypatch):
    from membrane import energy as E
    from membrane import self_loop as SL
    srv = types.SimpleNamespace(entity_store=EntityStore(path=str(tmp_path / "e.json"), shared=False),
                                _models_path=str(tmp_path / "m.json"), paused=False, efield=None)
    SL.bind(srv)
    SL.set_mode(srv, "on")
    seen = []
    monkeypatch.setattr(B, "exchange", lambda s, report: seen.append(dict(report)) or {"energy_delta": 0})
    state = {"asleep": False}
    monkeypatch.setattr(E, "asleep", lambda s: state["asleep"])
    real_tick = E.tick
    monkeypatch.setattr(E, "tick", lambda s, now, cpu_s=0.0: state.update(asleep=True))
    SL.step(srv)
    assert [r["awake"] for r in seen] == [True, False] and seen[-1]["asleep"] is True


def test_the_old_methods_are_retired_once(tmp_path):
    from membrane import self_loop as SL
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, _models_path=str(tmp_path / "m.json"), paused=False, efield=None)
    SL.bind(srv)
    me = SL._eid()
    for name in ("expect", "try", "notice", "reach", "nudge", "roll", "meet"):
        m = QuantumEntity(eid=MEMBRANE_NS + f"tool:self.{name}", cls="tool",
                          properties={"name": f"self.{name}", "method_name": name, "method_of": [me],
                                      "command": f"self::{name} {{{{seed}}}}"})
        est.upsert(m)
    SL.bind(srv)
    assert not any((d.properties or {}).get("method_of") == [me] for d in est.docs.values())


def test_accept_keeps_a_branch_ids_case(tmp_path):
    from membrane import self_loop as SL
    srv = types.SimpleNamespace(entity_store=EntityStore(path=str(tmp_path / "e.json"), shared=False),
                                _models_path=str(tmp_path / "m.json"), paused=False, efield=None)
    SL.bind(srv)
    assert SL.accept(srv, "branch:Component:toy")["id"] == "branch:Component:toy"


def test_the_card_carries_the_learners_counts(seat):
    """A diagnostic: the card says what the learner saw and why it passed, so a
    Blurp that earns nothing can be read, not guessed."""
    est = seat.entity_store
    B.exchange(seat, {"awake": True, "mode": "on"})
    e = _toy(est, "e")
    _turn(seat, (e, "a", 1, "person:op"), (e, "b", 2, "llm"))
    card = B.augment(seat, {"name": "Blurp", "level": 0})
    assert card["level"] == 0
    assert card["learner"]["seen"] >= 2 and card["learner"]["defined"] >= 1
    assert card["learner"]["candidates"] >= 1
