"""The child skill tree comes back (Dima's cards, 2026-10-30): the 13 skills of the
old ladder with their prerequisites, each EARNED by a kind of guess that keeps
coming true on any toy, ACCEPTED by the operator (`branch:skill:<id>`), and the
toys open under the skills (the tower from the start, the ball and the mirror once
first reach is granted). The roots are earned on Blurp's own body following the
current (card 1, C): feel itself = its own move lands where it guessed, look = it
guesses where the current is, tell things apart = it guesses the current's push,
not its own move -- each reading scored by closer-or-not, no new threshold."""
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import ch_blurp as B  # noqa: E402
from membrane import ch_body as BODY  # noqa: E402
from membrane import ch_learn as L  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import writes as W  # noqa: E402
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
    seen = []
    W.on_write(seen.append)
    srv.seen = seen
    yield srv
    W.off_write(seen.append)
    srv.ch_vm.detach()
    TB._SEAT["srv"] = None


def _mine(srv):
    return [t for t in srv.ch_vm.triggers() if (t.properties or {}).get("owner") == CH.SELF]


def _kinds(srv, before=()):
    return {(t.properties or {}).get("kind") for t in _mine(srv) if t.eid not in before}


def _nodes(srv):
    return {n["id"]: n for n in B.augment(srv, {"header": {}, "tree": []})["tree"]}


def _v(*xs):
    v = np.array(xs, dtype=float)
    return v / np.linalg.norm(v)


def _body_writes(srv):
    return [(w["prop"], w["writer"]) for w in srv.seen if w.get("eid") == BODY.BODY and w.get("prop")]


def _pay(srv, kind):
    lr = srv.ch_vm.learner
    for t in _mine(srv):
        if (t.properties or {}).get("kind") == kind:
            lr._hit(t.eid)
            lr._hit(t.eid)


# ---- the tree --------------------------------------------------------------------------------

def test_the_thirteen_skills_come_back_with_their_prerequisites(seat):
    n = _nodes(seat)
    ids = {i[len("branch:skill:"):] for i in n}
    assert ids == {"feel", "reach", "kinds", "patience", "shift", "nudge", "meet", "turns",
                   "expect", "mirror", "change", "speak", "invent"}
    assert n["branch:skill:reach"]["name"] == "look"
    assert n["branch:skill:nudge"]["name"] == "first reach"
    assert set(n["branch:skill:meet"]["prereqs"]) == {"branch:skill:reach", "branch:skill:nudge"}
    assert n["branch:skill:feel"]["branch"] == "self" and n["branch:skill:meet"]["branch"] == "others"
    assert n["branch:skill:feel"]["prereqs"] == []


def test_a_skill_with_no_kind_of_guess_yet_is_not_built(seat):
    n = _nodes(seat)
    for i in ("shift", "turns", "expect", "change", "speak", "invent"):
        assert n[f"branch:skill:{i}"]["built"] is False
    for i in ("feel", "reach", "kinds", "patience", "nudge", "meet", "mirror"):
        assert n[f"branch:skill:{i}"]["built"] is True


def test_a_skill_above_an_unearned_one_is_locked(seat):
    n = _nodes(seat)
    assert n["branch:skill:feel"]["state"] == "open"
    assert n["branch:skill:nudge"]["state"] == "locked"          # feel itself not earned yet


def test_a_skill_is_earned_when_a_guess_of_its_kind_pays_over_earned_prerequisites(seat):
    TB.add(seat, "tower", params={"n": 3})
    TB.nudge("toy:tower", block=3); seat.ch_vm.drain()
    _pay(seat, "nudge")
    n = _nodes(seat)
    assert n["branch:skill:nudge"]["state"] == "locked"           # paying, but feel itself is unearned
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    BODY.read(seat, at=_v(1, 1, 0), cur=_v(0, 1, 0), near="w:a", gen=0); seat.ch_vm.drain()
    _pay(seat, "feel")
    n = _nodes(seat)
    assert n["branch:skill:feel"]["state"] == "earned"
    assert n["branch:skill:nudge"]["state"] == "earned"
    assert "branch:skill:nudge" in B.augment(seat, {"header": {}, "tree": []})["awaiting"]


def test_accepting_a_skill_grants_it_and_the_level_counts_skills(seat):
    TB.add(seat, "tower", params={"n": 3})
    assert B.grant(seat, "skill:nudge")
    s = B.augment(seat, {"header": {}, "tree": []})
    n = {x["id"]: x for x in s["tree"]}
    assert n["branch:skill:nudge"]["accepted"] is True
    assert s["level"] == 1 and "branch:skill:nudge" in s["accepted"]
    assert B.granted(seat, "skill:nudge")


# ---- the kinds of guess -----------------------------------------------------------------------

def test_the_toys_own_later_move_is_patience(seat):
    TB.add(seat, "die")
    seat.ch_vm.learner.toy_open = lambda name: True
    st = TB.peek("toy:die")
    TB.tick("toy:die", now=st["next_at"] + 1); seat.ch_vm.drain()
    assert _kinds(seat) == {"patience"}


def test_a_guess_on_the_mirror_is_recognize_itself(seat):
    est = seat.entity_store
    m = TB._put(est, "toy:mirror", "mirror")
    assert L.kind_of(est, {"writer": "self", "prop": "nudged"}, {"eid": m, "prop": "glass"}) == "mirror"
    assert L.kind_of(est, {"writer": "world", "prop": "ticked"}, {"eid": m, "prop": "stranger"}) == "mirror"


def test_kinds_still_read_who_moved_it(seat):
    est = seat.entity_store
    ball = TB._put(est, "toy:ball", "ball")
    w = {"eid": ball, "prop": "side"}
    assert L.kind_of(est, {"writer": "self"}, w) == "nudge"
    assert L.kind_of(est, {"writer": "person:Dima"}, w) == "meet"
    assert L.kind_of(est, {"writer": "model"}, w) == "meet"
    assert L.kind_of(est, {"writer": "world"}, w) == "patience"


# ---- the body following the current (card 1, C) ------------------------------------------------

def test_the_first_reading_only_places_the_body(seat):
    assert BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0) is None
    assert not _body_writes(seat)


def test_its_own_move_is_told_first_then_where_the_current_is_and_closer(seat):
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    seat.seen.clear()
    r = BODY.read(seat, at=_v(1, 1, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    assert r == {"by": ["self"], "closer": True}
    ws = _body_writes(seat)
    assert ws[0] == ("nudged", "self")
    assert {p for p, _ in ws} == {"nudged", "at", "closer"}
    chains = {(w.get("chain") or {}).get("id") for w in seat.seen if w.get("eid") == BODY.BODY}
    assert len(chains) == 1 and None not in chains               # one chain: the cause, then the effect


def test_the_current_moving_is_the_worlds_push(seat):
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    seat.seen.clear()
    r = BODY.read(seat, at=_v(1, 0, 0), cur=_v(1, 1, 0), near="w:b", gen=1)
    assert r == {"by": ["world"], "closer": True}
    assert _body_writes(seat)[0] == ("ticked", "world")


def test_moving_away_is_not_closer(seat):
    BODY.read(seat, at=_v(1, 1, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    assert BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0) == {"by": ["self"], "closer": False}


def test_nothing_moved_nothing_told(seat):
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    seat.seen.clear()
    assert BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0) is None
    assert not _body_writes(seat)


def test_the_body_teaches_the_three_roots_with_no_toy_open(seat):
    seat.ch_vm.learner.toy_open = lambda name: False
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    BODY.read(seat, at=_v(1, 1, 0), cur=_v(0, 1, 0), near="w:a", gen=0); seat.ch_vm.drain()
    assert {"feel", "reach"} <= _kinds(seat)
    before = {t.eid for t in _mine(seat)}
    BODY.read(seat, at=_v(1, 1, 0), cur=_v(1, 1, 1), near="w:b", gen=1); seat.ch_vm.drain()
    assert "kinds" in _kinds(seat, before)
    assert all((t.properties or {}).get("toy") for t in _mine(seat))   # they fire while toys are the world


def test_a_body_guess_comes_true_and_pays(seat):
    BODY.read(seat, at=_v(1, 0, 0), cur=_v(0, 1, 0), near="w:a", gen=0)
    BODY.read(seat, at=_v(1, 1, 0), cur=_v(0, 1, 0), near="w:a", gen=0); seat.ch_vm.drain()
    BODY.read(seat, at=_v(1, 2, 0), cur=_v(0, 1, 0), near="w:a", gen=0); seat.ch_vm.drain()
    lr = seat.ch_vm.learner
    feel = [t for t in _mine(seat) if (t.properties or {}).get("kind") == "feel"]
    assert any(lr.hits(t.eid) for t in feel)                       # closer again: the guess came true


def test_the_exchange_reads_the_body_where_its_newest_firing_put_it(seat, monkeypatch):
    got = []
    monkeypatch.setattr(BODY, "read", lambda srv, at, cur, near, gen: got.append((list(at), list(cur), near)))

    class _F:
        def get(self, fid):
            return types.SimpleNamespace(state=[1.0, 0.0, 0.0]) if fid == B.SEAT_FID else None

        def current(self):
            return np.array([0.0, 1.0, 0.0])

        def current_gen(self):
            return 1

        def recall(self, vec, k=1, **kw):
            return [("m:somewhere", 0.9)]
    seat.efield = _F()
    seat._field_pulse = types.SimpleNamespace(self_cycle=lambda *a: None)
    B.exchange(seat, {"awake": True, "mode": "on"})
    assert got == [([1.0, 0.0, 0.0], [0.0, 1.0, 0.0], "m:somewhere")]


# ---- the toys open under the skills -------------------------------------------------------------

def test_the_tower_is_open_from_the_start_ball_and_mirror_under_first_reach(seat):
    TB.add(seat, "tower", params={"n": 3})
    toys = {t["kind"]: t for t in B.toys(seat)}
    assert toys["tower"]["state"] == "current"
    assert toys["ball"]["state"] == "locked" and toys["mirror"]["state"] == "locked"
    assert B.grant(seat, "skill:nudge")
    toys = {t["kind"]: t for t in B.toys(seat)}
    assert toys["ball"]["state"] == "current" and toys["mirror"]["state"] == "current"
    assert TB.peek("toy:ball") is not None and TB.peek("toy:mirror") is not None   # switched on


def test_a_proposal_suggests_once_its_skill_is_granted(seat):
    got = []
    seat.ch_vm.learner.suggest = lambda tr, path, value: got.append(path)
    TB.add(seat, "tower", params={"n": 3})
    TB.nudge("toy:tower", block=3); seat.ch_vm.drain()
    TB.nudge("toy:tower", block=3); seat.ch_vm.drain()
    lr = seat.ch_vm.learner
    for t in _mine(seat):
        lr._bal[t.eid] = 5.0
    TB.nudge("toy:tower", block=3); seat.ch_vm.drain()
    assert not got                                                 # paying, not granted
    B.grant(seat, "skill:nudge")
    TB.nudge("toy:tower", block=3); seat.ch_vm.drain()
    assert got



def test_a_locked_toy_names_the_skill_that_opens_it(seat):
    TB.add(seat, "tower", params={"n": 3})
    rows = {t["kind"]: t for t in B.augment(seat, {"header": {}, "tree": []})["toys"]}
    assert rows["ball"]["under_name"] == "first reach" and rows["tower"]["under_name"] is None
    assert rows["tower"]["built"] is True and rows["tower"]["have"] is True


# ---- Blurp anew on the seat's first boot of the child tree (Dima, 2026-10-30) ----------------

def _self_trigger(srv, name="old"):
    return srv.ch_vm.define(name, when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]],
                            write=["?x.b", "1"], owner=CH.SELF)


def test_anew_first_boot_clears_what_blurp_learned(seat):
    B.grant(seat, "Component:toy")                    # an old branch grant
    mine = _self_trigger(seat)
    theirs = seat.ch_vm.define("op", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]],
                               write=["?x.c", "1"])
    TB.add(seat, "tower", params={"n": 3})
    TB.nudge("toy:tower", block=3); seat.ch_vm.drain()
    assert TB.list_toys()[0]["counts"]
    seat._ch_grants = None                            # a fresh boot reads the file again
    out = B.anew(seat)
    assert out["anew"] is True
    assert B.grants(seat) == set() and B.level(seat) == 0
    assert mine not in seat.entity_store.docs and theirs in seat.entity_store.docs
    assert not any(TB.list_toys()[0]["counts"].values())   # the toy is back to its start
    f = B._grants_file(seat)
    assert not f.exists() and list(f.parent.glob("ch-grants.*.json"))   # archived, not deleted


def test_anew_runs_once(seat):
    assert B.anew(seat)["anew"] is True
    B.grant(seat, "skill:nudge")
    mine = _self_trigger(seat)
    assert B.anew(seat)["anew"] is False              # the second boot keeps what was earned since
    assert "skill:nudge" in B.grants(seat) and mine in seat.entity_store.docs
