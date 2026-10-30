"""#751 group 1: expect a reply, time its turn, speak. A reply is a move by
someone else on the toy Blurp just moved (and its turn the reverse), so the
learner links a toy's move to the previous mover's chain on that toy; speak is
earned by a suggestion the LLM adopts."""
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
from membrane import ch_learn as L  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None, paused=False)
    srv.ch_vm = CH.VM(est)
    srv.ch_vm.attach()
    TB.bind(srv)
    B.attach(srv)
    srv.ch_purse.awake = True
    srv.ch_vm.learner.toy_open = lambda name: True
    yield srv
    srv.ch_vm.detach()
    TB._SEAT["srv"] = None


def _mine(srv):
    return {t.eid: t for t in srv.ch_vm.triggers() if (t.properties or {}).get("owner") == CH.SELF}


def _new_kinds(srv, before):
    return {(t.properties or {}).get("kind") for e, t in _mine(srv).items() if e not in before}


def test_kind_of_reads_both_movers():
    def k(cw, ww):
        return L.kind_of(None, {"writer": cw, "eid": "x"}, {"writer": ww, "eid": "x"})
    assert k("self", "self") == "nudge"
    assert k("self", "person:Dima") == "expect"
    assert k("self", "model") == "expect"
    assert k("person:Dima", "self") == "turns"
    assert k("model", "self") == "turns"
    assert k("person:Dima", "person:Dima") == "meet"
    assert k("world", "self") == "patience"


def test_a_reply_to_blurps_serve_is_expect(seat):
    TB.add(seat, "ball")
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    before = set(_mine(seat))
    TB.poke("toy:ball", "roll", by="Dima"); seat.ch_vm.drain()
    assert "expect" in _new_kinds(seat, before)


def test_blurps_turn_after_an_others_move_is_turns(seat):
    TB.add(seat, "ball")
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    TB.poke("toy:ball", "roll", by="Dima"); seat.ch_vm.drain()
    before = set(_mine(seat))
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    assert "turns" in _new_kinds(seat, before)


def test_the_same_mover_twice_links_nothing(seat):
    TB.add(seat, "ball")
    TB.poke("toy:ball", "roll", by="Dima"); seat.ch_vm.drain()
    TB.poke("toy:ball", "roll", by="Dima"); seat.ch_vm.drain()
    assert _new_kinds(seat, set()) == {"meet"}     # never paired across the two pokes


def test_an_expected_reply_pays(seat):
    TB.add(seat, "ball")
    for _ in range(3):
        TB.nudge("toy:ball"); seat.ch_vm.drain()
        TB.poke("toy:ball", "roll", by="Dima"); seat.ch_vm.drain()
    lr = seat.ch_vm.learner
    assert any(lr.hits(e) for e, t in _mine(seat).items() if (t.properties or {}).get("kind") == "expect")


def _ball(srv):
    return next(e for e in srv.entity_store.docs.values()
                if L.toy_of(srv.entity_store, e.eid) and "side" in (e.properties or {}))


def test_speak_is_earned_by_an_adopted_suggestion(seat):
    TB.add(seat, "ball")
    TB.nudge("toy:ball"); seat.ch_vm.drain()
    cand = next(iter(_mine(seat)))
    lr = seat.ch_vm.learner
    lr.adopted = lambda w: [cand]
    ball = _ball(seat)
    lr.observe({"path": f"{ball.eid}.side", "eid": ball.eid, "prop": "side", "old": "other", "new": "self",
                "writer": "llm", "chain": {"id": "chat-1"}})
    assert lr.adoptions(cand) == 1
    seat._ch_grants = {"skill:turns", "skill:expect"}
    speak = next(n for n in B.tree(seat) if n["id"] == "branch:skill:speak")
    assert speak["built"] and speak["state"] == "earned"


def test_speak_waits_without_an_adoption(seat):
    seat._ch_grants = {"skill:turns", "skill:expect"}
    speak = next(n for n in B.tree(seat) if n["id"] == "branch:skill:speak")
    assert speak["built"] and speak["state"] == "open"
