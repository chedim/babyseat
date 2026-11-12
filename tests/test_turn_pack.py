"""A turn is an entity (the operator, 2026-11-12): `Turn:<id>` holds what it touched, its chat's `Ctx:<sid>` holds the entered
frames and the promoted subjects, and the pronouns are getters on the turn (packs/turn.c1f) with the rules callctx.py had:
innermost first -- the turn's touched things, then the entered frames, then the chat's promoted subjects. Only concrete
entities bind (never a concept, a word, a session, a register); he/she bind a person, there/here a place; they is the list of
the concrete things touched this turn."""
import os
import sys
import time
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


@pytest.fixture
def seat(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=types.SimpleNamespace(),
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    FL.start(srv)
    AD.start(srv)
    FL.boot_started(est, facts={})
    yield srv, est
    AD.stop()
    FL.stop(srv)


def _until(fn, timeout=6.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v is not None and v != "":
            return v
        time.sleep(0.05)
    return fn()


def _make(est, eid, cls, **props):
    est.upsert(QuantumEntity(eid=eid, cls=cls, properties=props), embedder=None)


def _p(est, eid, k):
    return (est.docs[eid].properties or {}).get(k)


def _turn(est, touched=(), ctx=None, frames=(), promoted=None):
    _make(est, "Ctx:c1", "Ctx", frames=list(frames), **({"promoted": promoted} if promoted else {}))
    _make(est, "Turn:t1", "Turn", sid="c1", ctx="Ctx:c1", touched=list(touched), state="running")
    return "Turn:t1"


def test_it_is_the_last_concrete_thing_touched_this_turn(seat):
    srv, est = seat
    t = _turn(est, touched=["membrane::f:router.py", "membrane::goal:old"])          # newest first: router.py was last
    assert _until(lambda: _p(est, t, "it")) == "membrane::f:router.py"
    assert _p(est, t, "this") == "membrane::f:router.py"


def test_a_concept_a_word_a_session_a_register_never_bind(seat):
    srv, est = seat
    t = _turn(est, touched=["membrane::concept:battery", "w:battery", "session:now", "s:?x", "seat:self", "membrane::register:r", "membrane::f:a.py"])
    assert _until(lambda: _p(est, t, "it")) == "membrane::f:a.py"
    assert _p(est, t, "they") is None                                                   # only one concrete thing: not a plural


def test_they_is_the_concrete_things_touched_newest_first_and_he_and_there_bind_their_kinds(seat):
    srv, est = seat
    t = _turn(est, touched=["membrane::place:galley", "membrane::person:dima", "membrane::f:b.py", "membrane::f:a.py"])
    assert _until(lambda: _p(est, t, "they")) == ["membrane::place:galley", "membrane::person:dima", "membrane::f:b.py", "membrane::f:a.py"]
    assert _p(est, t, "he") == "membrane::person:dima" and _p(est, t, "she") == "membrane::person:dima"
    assert _p(est, t, "there") == "membrane::place:galley" and _p(est, t, "here") == "membrane::place:galley"
    assert _p(est, t, "it") == "membrane::f:b.py"                                       # a person and a place are not "it"


def test_an_entered_frame_binds_it_and_the_outer_one_is_back_when_it_exits(seat):
    srv, est = seat
    frames = [{"eid": "membrane::goal:inner", "bind": {"it": "membrane::goal:inner"}},        # innermost first
              {"eid": "membrane::goal:outer", "bind": {"it": "membrane::goal:outer"}}]
    t = _turn(est, touched=[], frames=frames)
    assert _until(lambda: _p(est, t, "it")) == "membrane::goal:inner"
    d = est.docs["Ctx:c1"]
    d.properties = {**d.properties, "frames": frames[1:]}
    est.upsert(d, embedder=None)
    assert _until(lambda: "membrane::goal:outer" if _p(est, t, "it") == "membrane::goal:outer" else None) == "membrane::goal:outer"


def test_the_turns_touch_beats_the_frame_and_the_chats_promoted_subject_comes_last(seat):
    srv, est = seat
    t = _turn(est, touched=["membrane::f:now.py"], frames=[{"eid": "membrane::goal:g", "bind": {"it": "membrane::goal:g"}}],
              promoted={"it": "membrane::f:before.py", "that": "membrane::f:earlier.py", "age": 0})
    assert _until(lambda: _p(est, t, "it")) == "membrane::f:now.py"
    _make(est, "Ctx:c2", "Ctx", frames=[], promoted={"it": "membrane::f:before.py", "that": "membrane::f:earlier.py", "age": 0})
    _make(est, "Turn:t2", "Turn", sid="c2", ctx="Ctx:c2", touched=[], state="running")
    assert _until(lambda: _p(est, "Turn:t2", "it")) == "membrane::f:before.py"
    assert _p(est, "Turn:t2", "that") == "membrane::f:earlier.py"
    assert _p(est, "Turn:t2", "this") == "membrane::f:before.py"


def test_an_unbound_pronoun_is_none(seat):
    srv, est = seat
    t = _turn(est, touched=[])
    time.sleep(0.5)
    assert _p(est, t, "it") is None and _p(est, t, "they") is None and _p(est, t, "he") is None
