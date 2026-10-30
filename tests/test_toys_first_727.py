"""#727: Blurp learns toys first. A toy's change is a field write chain (who
moved it as the cause, its reading as the effect), the VM's learner guesses
and earns on toys only (commands gated off, kept), each toy is its own branch,
and a granted toy opens the next one in #337's order."""
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
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

BALL = MEMBRANE_NS + "Component:toy:ball"


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
    seen = []
    W.on_write(seen.append)
    srv.seen = seen
    yield srv
    W.off_write(seen.append)
    srv.ch_vm.detach()
    TB._SEAT["srv"] = None


def _toy_writes(srv, eid):
    return [w for w in srv.seen if w.get("eid") == eid and w.get("prop")]


def _cands(srv):
    return [t for t in srv.ch_vm.triggers() if (t.properties or {}).get("owner") == CH.SELF]


# ---- 3. a toy's change is a field write chain --------------------------------------------------

def test_a_persons_roll_is_a_chain_cause_first(seat):
    assert TB.add(seat, "ball")["ok"]
    TB.nudge("toy:ball")                       # self has it at birth: roll it over
    seat.seen.clear()
    assert TB.poke("toy:ball", "roll", by="Dima")["ok"]
    ws = _toy_writes(seat, BALL)
    props = [w["prop"] for w in ws]
    assert props == ["poked", "side"]          # who moved it, then what it shows
    assert ws[1]["new"] == "self"
    assert {w["writer"] for w in ws} == {"person:Dima"}
    chains = {(w.get("chain") or {}).get("id") for w in ws}
    assert len(chains) == 1 and None not in chains


def test_blurps_nudge_is_the_cause_the_state_the_effect(seat):
    TB.add(seat, "ball")
    seat.seen.clear()
    assert TB.nudge("toy:ball")["changed"]
    ws = _toy_writes(seat, BALL)
    assert [w["prop"] for w in ws] == ["nudged", "side"]
    assert ws[1]["new"] == "other"
    assert {w["writer"] for w in ws} == {"self"}


def test_the_worlds_tick_writes_too(seat):
    TB.add(seat, "die")
    st = TB.peek("toy:die")
    seat.seen.clear()
    assert TB.tick("toy:die", now=st["next_at"] + 1)
    ws = _toy_writes(seat, MEMBRANE_NS + "Component:toy:die")
    assert [w["prop"] for w in ws] == ["ticked", "face"]
    assert {w["writer"] for w in ws} == {"world"}


def test_a_tower_block_shows_its_own_standing(seat):
    TB.add(seat, "tower", params={"n": 3})
    seat.seen.clear()
    TB.nudge("toy:tower", block=2)
    b2 = MEMBRANE_NS + "Component:toy:tower:b2"
    ws = _toy_writes(seat, b2)
    assert [w["prop"] for w in ws] == ["nudged", "standing"] and ws[1]["new"] is False


def test_no_seat_no_writes_the_toy_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    TB._SEAT["srv"] = None
    TB.create("ball", "toy:ball")
    assert TB.nudge("toy:ball")["changed"]


# ---- 1. toys are the learner's world -------------------------------------------------------------

def test_toys_teach_by_anyone_and_commands_are_gated_off(seat):
    TB.add(seat, "tower", params={"n": 3})            # the first toy in the order: open
    TB.nudge("toy:tower", block=3)
    seat.ch_vm.drain()
    assert _cands(seat), "a toy's chain teaches, whoever moved it"
    n = len(_cands(seat))
    x = QuantumEntity(eid=MEMBRANE_NS + "Component:x", cls="Component", properties={"name": "x"})
    seat.entity_store.upsert(x)
    x = seat.entity_store.docs[x.eid]
    with W.source("person", "t"), W.as_writer("llm"):
        x.properties["a"] = 1
        seat.entity_store.upsert(x)
        x.properties["b"] = 2
        seat.entity_store.upsert(x)
    seat.ch_vm.drain()
    assert len(_cands(seat)) == n                      # the LLM's command writes teach nothing now
    assert seat.ch_vm.learner.counts["pass:not_toy"] >= 2


def test_each_toy_is_its_own_branch(seat):
    TB.add(seat, "tower", params={"n": 3})
    TB.nudge("toy:tower", block=3)
    seat.ch_vm.drain()
    assert {(t.properties or {}).get("branch") for t in _cands(seat)} == {"toy:tower"}
    est = seat.entity_store
    assert L.branch(est, MEMBRANE_NS + "Component:toy:tower:b2.standing") == "toy:tower"
    assert L.branch(est, "membrane::ha:light.x") == "ha:light"     # not a toy: kind + type, as before


def test_the_ball_is_learned_and_pays(seat):
    TB.add(seat, "ball")
    seat.ch_vm.learner.toy_open = lambda name: True
    for _ in range(4):
        TB.nudge("toy:ball")
        seat.ch_vm.drain()
        TB.poke("toy:ball", "roll", by="Dima")
        seat.ch_vm.drain()
    lr = seat.ch_vm.learner
    paying = [t for t in _cands(seat) if lr.state(t.eid) == "proposal"]
    assert paying, "nudged -> side 'other' is predicted and pays"
    assert all((t.properties or {}).get("branch") == "toy:ball" for t in paying)


def test_a_locked_toy_teaches_nothing(seat):
    TB.add(seat, "tower", params={"n": 3})
    TB.add(seat, "ball")                       # the ball is 7th: locked until the toys before it are granted
    TB.nudge("toy:ball")
    seat.ch_vm.drain()
    assert not _cands(seat)
    assert seat.ch_vm.learner.counts["pass:locked_toy"] >= 1


def test_an_old_command_candidate_no_longer_fires(seat):
    vm = seat.ch_vm
    x = QuantumEntity(eid=MEMBRANE_NS + "Component:x", cls="Component", properties={"name": "x"})
    seat.entity_store.upsert(x)
    vm.define("cand-old", owner=CH.SELF, when=[["changed", "?x.a"]], where=[["isa", "?x", "Component"]],
              write=["?x.b", "1"])
    t = seat.entity_store.docs[CH.MEMBRANE_NS + f"{CH.CLASS}:cand-old"]
    t.properties["branch"] = "shell:gh"
    seat.entity_store.upsert(t)
    x = seat.entity_store.docs[x.eid]
    with W.source("person", "t"), W.as_writer("llm"):
        x.properties["a"] = 5
        seat.entity_store.upsert(x)
    vm.drain()
    assert vm.learner.counts["fire:gated"] >= 1 and vm.learner.counts["fire"] == 0


def test_commands_come_back_with_the_switch(seat, monkeypatch):
    monkeypatch.setattr(L, "WORLD", "commands")
    x = QuantumEntity(eid=MEMBRANE_NS + "Component:x", cls="Component", properties={"name": "x"})
    seat.entity_store.upsert(x)
    x = seat.entity_store.docs[x.eid]
    with W.source("person", "t"), W.as_writer("llm"):
        x.properties["a"] = 1
        seat.entity_store.upsert(x)
        x.properties["b"] = 2
        seat.entity_store.upsert(x)
    seat.ch_vm.drain()
    assert _cands(seat)


# ---- 2. earning a toy is how Blurp grows ---------------------------------------------------------

def test_the_order_is_337s_without_the_self_moving_toys():
    # #727, the operator: no self-moving toys -- slow, fast, burst, die and drift change on
    # their own clock, so nothing Blurp guesses or does can earn them
    assert B.TOY_ORDER == ("tower", "ball", "mirror")


def test_a_self_moving_toy_is_never_open(seat):
    TB.add(seat, "slow")
    TB.add(seat, "tower", params={"n": 3})
    s = B.augment(seat, {"header": {}, "tree": []})
    assert "slow" not in {t["kind"] for t in s["toys"]}
    assert not seat.ch_vm.learner.toy_open("toy:slow")


def test_the_sheet_shows_the_toys_current_and_next(seat):
    TB.add(seat, "tower", params={"n": 3})
    s = B.augment(seat, {"header": {}, "tree": []})
    toys = {t["kind"]: t for t in s["toys"]}
    assert toys["tower"]["state"] == "current" and toys["ball"]["state"] == "locked"
    assert toys["mirror"]["state"] == "locked"         # the ball and the mirror open under first reach
    assert s["toy_current"] == "tower" and s["toy_next"] == "ball"
    assert all(not n["id"].startswith("branch:shell") for n in s["tree"])


def test_accepting_first_reach_opens_the_ball_and_the_mirror_and_provisions_them(seat):
    TB.add(seat, "tower", params={"n": 3})
    assert B.grant(seat, "skill:nudge")
    assert TB.peek("toy:ball") is not None             # the toys under it are switched on
    s = B.augment(seat, {"header": {}, "tree": []})
    toys = {t["kind"]: t for t in s["toys"]}
    assert toys["tower"]["state"] == "current"
    assert toys["ball"]["state"] == "current" and toys["mirror"]["state"] == "current"
    assert s["level"] == 1
    lr = seat.ch_vm.learner
    assert lr.toy_open("toy:ball") and lr.toy_open("toy:tower") and lr.toy_open("toy:mirror")


def test_a_seat_with_old_toy_names_maps_them_by_kind(seat):
    TB.add(seat, "ball", name="w:ball")
    TB.add(seat, "tower", params={"n": 3})
    B.grant(seat, "skill:nudge")
    s = B.augment(seat, {"header": {}, "tree": []})
    toys = {t["kind"]: t for t in s["toys"]}
    assert toys["ball"]["name"] == "w:ball" and toys["ball"]["state"] == "current"
    assert TB.peek("toy:ball") is None                 # the seat had one: none added


def test_only_what_the_toy_tells_teaches(seat):
    TB.add(seat, "tower", params={"n": 3})
    ent = seat.entity_store.docs[MEMBRANE_NS + "Component:toy:tower"]
    with W.source("person", "t"), W.as_writer("llm"):
        ent.properties["_last_reading"] = {"standing": 3}
        seat.entity_store.upsert(ent)
        ent.properties["readings"] = [1, 2]
        seat.entity_store.upsert(ent)
    seat.ch_vm.drain()
    assert not _cands(seat)
    assert seat.ch_vm.learner.counts["pass:not_toy"] >= 2


# ---- the review on #728 --------------------------------------------------------------------------

def test_the_open_toys_are_read_once_not_per_write(seat, monkeypatch):
    # the learner asks toy_open on every toy write: the toybox files are read only
    # when the roster or the grants change, never per ask
    TB.add(seat, "tower", params={"n": 3})
    calls = []
    real = TB.list_toys
    monkeypatch.setattr(TB, "list_toys", lambda: calls.append(1) or real())
    lr = seat.ch_vm.learner
    for _ in range(20):
        assert lr.toy_open("toy:tower")
    B.augment(seat, {"header": {}, "tree": []})
    assert len(calls) <= 1
    TB.add(seat, "ball")                               # the roster changed: read again
    assert lr.toy_open("toy:tower")
    assert len(calls) == 2
    B.grant(seat, "skill:nudge")                       # a grant changes what is open
    assert lr.toy_open("toy:ball")


def test_the_toys_cache_never_keys_on_a_reused_dict_id(seat, monkeypatch):
    # CPython reuses a freed object's id: when the roster moves, a new `have` dict can
    # come back with the old one's id, so the cache must key on what moved (the roster)
    have = {}
    monkeypatch.setattr(B, "_seat_toys", lambda: have)          # the same object, same id
    assert not B.toys(seat)[0]["on_seat"]
    have["tower"] = "toy:tower"                                   # the roster moved
    TB._ROSTER[0] += 1
    assert B.toys(seat)[0]["on_seat"]


def test_the_level_counts_skill_grants_only(seat):
    TB.add(seat, "tower", params={"n": 3})
    B.grant(seat, "shell:gh")
    B.grant(seat, "shell:ls")
    s = B.augment(seat, {"header": {}, "tree": []})
    assert s["level"] == 0 and s["header"]["title"] == "Lv 0"
    assert B.grants(seat) >= {"shell:gh", "shell:ls"}  # kept on file for later
    B.grant(seat, "toy:tower")                         # a toy grant from before the skills: kept, not a level
    assert B.augment(seat, {"header": {}, "tree": []})["level"] == 0
    B.grant(seat, "skill:nudge")
    assert B.augment(seat, {"header": {}, "tree": []})["level"] == 1


def test_a_change_survives_another_toys_load_in_the_same_thread(seat):
    TB.add(seat, "ball")
    TB.add(seat, "tower", params={"n": 3})
    a = TB._load("toy:ball")
    TB._note(a, 1.0, "Dima", "roll")
    TB._load("toy:tower")                              # another toy loaded before A saves
    TB._load("toy:ball")                               # or A itself reloaded
    seat.seen.clear()
    TB._save(a)
    assert _toy_writes(seat, BALL)                     # A's change was still told
    seat.seen.clear()
    TB._save(TB._load("toy:tower"))                    # and never told against the tower
    assert not [w for w in seat.seen if w.get("eid") == BALL]
