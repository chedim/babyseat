"""#333 part 3f: TIME ITS TURN (rung 7) and EXPECT A REPLY (rung 8), rebuilt on
meet's other; the `rebuilt()` test helper retires.

Meet's other is the thing meet an other's pass named (its `achieve` line's
eid): the thing whose reply looks beat its tempo, the changes not Blurp's.

- TURNS: when Blurp is aimed at the other and closing in, it nudges only in
  the age bin its bandit picks (_turn_choose: a Thompson draw, TURN_EXPLORE of
  the time a random bin). The age is the time since its own looks last saw
  the other change (a look with no nudge of its own since the previous one:
  a check's change is its own nudge's). TURN_BINS (20, 60, 180) s. Between a
  timed nudge and its follow-up the turn is open: no other nudge at the other.
  The reward is the follow-up's change. The test is _turn_score, unchanged,
  over RUNG7_NUDGES timed nudges (RUNG7_PAY_Z 5.0, the ball's bar from #278,
  now the only one).
- EXPECT: p_self (the ridge logistic over _r8_design: the running rate, the
  recent rate, the last outcome, the freshness exp(-gap / RUNG8_FRESH_S) of
  the age at the nudge) predicts each follow-up at the other, before it looks.
  The test is today's: Brier skill over the running rate >= RUNG8_MARGIN over
  the last RUNG8_LOOKS predicted follow-ups. The model advisor stays out.
- Prerequisites: turns (meet), expect (meet); speak keeps (turns, expect)."""
import datetime as dt
import json
import math
import os
import random
import sys
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

from tests import _self_cap  # noqa: E402
from tests._self_cap import world  # noqa: E402

DIM = 16
T0 = 1_700_000_000.0
NS = MEMBRANE_NS + "Component:"
BALL = NS + "toy:ball"
DRIFT = NS + "toy:drift"


def _meet(eid, ts=T0 - 1000.0):
    """Meet's pass, naming its other (as _open_rungs writes it)."""
    return [{"ts": ts, "kind": "achieve", "id": "meet", "rung": 6, "skill": 0.5, "ladder": SL.LADDER_V,
             "eid": eid}]


def _turns(eid, n, reply, seed=1, t0=T0):
    """n timed nudges at eid as the seat records them, each picked by the
    bandit over the rows before it: the nudge (its bin, pick, best and the
    waiting time per bin), its check a second later (nudged 1) and the
    follow-up FOLLOW_S after the check (follow, nudged 0) that found a change
    with chance reply(bin)."""
    rng = random.Random(seed)
    recs = []
    for i in range(n):
        t = t0 + i * 400.0
        pick, best = SL._turn_choose(SL._turn_nudges(recs, eid), random.Random(f"turn:{i}"))
        dwell = [0] * (len(SL.TURN_BINS) + 1)
        while True:                                 # the moments it could have nudged in, until its bin came
            b = rng.randrange(len(dwell))
            dwell[b] += 1
            if b == pick:
                break
        recs.append({"ts": t, "kind": "nudge", "n": i, "eid": eid, "rc": 0, "bin": pick, "pick": pick,
                     "best": best, "dwell": dwell, "age": None})
        recs.append({"ts": t + 1, "kind": "reach", "eid": eid, "p": 0.1, "p_n": 0.9, "nudged": 1,
                     "answer": "changed", "by": ["self"]})
        x = rng.random() < reply(pick)
        recs.append({"ts": t + 1 + SL.FOLLOW_S, "kind": "reach", "eid": eid, "p": 0.1, "p_n": 0.1, "nudged": 0,
                     "follow": True, "answer": "changed" if x else "unchanged"})
    return recs


def _expects(eid, n, reply, seed=2, t0=T0, predict=True):
    """n follow-ups at eid, each after a nudge whose age (the gap) is drawn
    from 5-300 s, each predicted by p_self before it looks (when `predict`),
    found changed with chance reply(gap)."""
    rng = random.Random(seed)
    recs = []
    for i in range(n):
        t = t0 + i * 600.0
        gap = rng.uniform(5.0, 300.0)
        recs.append({"ts": t, "kind": "nudge", "n": i, "eid": eid, "rc": 0})
        recs.append({"ts": t + 1, "kind": "reach", "eid": eid, "p": 0.1, "p_n": 0.9, "nudged": 1,
                     "answer": "changed", "by": ["self"]})
        r = {"ts": t + 1 + SL.FOLLOW_S, "kind": "reach", "eid": eid, "p": 0.1, "p_n": 0.1, "nudged": 0,
             "follow": True, "gap": round(gap, 3)}
        if predict:
            r["p_self"] = SL._p_follow(recs, eid, gap)
        r["answer"] = "changed" if rng.random() < reply(gap) else "unchanged"
        recs.append(r)
    return recs


def _turn_taker(b):
    return 0.9 if b == 1 else 0.1


# ---- the approved values and the tree --------------------------------------------------------

def test_the_approved_values():
    assert SL.TURN_BINS == (20.0, 60.0, 180.0)
    assert SL.RUNG8_FRESH_S == 60.0
    assert not hasattr(SL, "RUNG8_SPOKE_S")                      # replaced
    assert SL.RUNG7_NUDGES == 100
    assert not hasattr(SL, "RUNG7_PUSHES")                       # renamed: the turns are nudges
    assert SL.RUNG7_PAY_Z == 5.0                                 # the ball's bar (#278), now the only one
    assert (SL.RUNG7_CONT_Z, SL.RUNG7_CONT_MARGIN, SL.RUNG7_PAY_MARGIN) == (3.0, 0.10, 0.05)
    assert (SL.TURN_EXPLORE, SL.TURN_MEMORY) == (0.1, 100)
    assert (SL.RUNG8_LOOKS, SL.RUNG8_MARGIN, SL.RUNG8_RIDGE, SL.RUNG8_FIT) == (100, 0.08, 10.0, 500)


def test_turns_and_expect_are_built_on_meet():
    for i, n in (("turns", 7), ("expect", 8)):
        a = SL.ACHIEVEMENTS[i]
        assert a["built"] is True and a["rung"] == n
        assert a["prereqs"] == ("meet",)
        assert i in SL.BUILT
        assert SL.PROGRESS[n] is getattr(SL, f"_rung{n}")
        assert SS.BUILT_AT[i]["pr"] == 481                          # rebuilt: built from this PR, not #281's
        assert SS.BUILT_AT[i]["merged"] == dt.datetime(2026, 10, 23, 9, 0, 44,      # the PR's opening time
                                                       tzinfo=dt.timezone.utc).timestamp()   # until its merge's
    assert SL.ACHIEVEMENTS["speak"]["prereqs"] == ("turns", "expect")
    assert SL.SKILL_PASS[7] == SL.RUNG7_PAY_MARGIN and SL.SKILL_PASS[8] == SL.RUNG8_MARGIN


def test_the_rebuilt_test_helper_is_retired():
    for name in ("rebuilt", "start_rebuilt", "stop_rebuilt"):
        assert not hasattr(_self_cap, name)


def test_meets_other_is_the_thing_meets_pass_named():
    assert SL._meet_other([]) is None
    assert SL._meet_other(_meet(BALL)) == BALL
    old = [{"ts": 1.0, "kind": "achieve", "id": "meet", "rung": 6, "skill": 1.0, "eid": DRIFT}]   # the old wiring's
    assert SL._meet_other(old) is None
    assert SL._meet_other(_meet(DRIFT, 1.0) + _meet(BALL, 2.0)) == BALL      # the latest pass


# ---- the turns' rows: a timed nudge, its check, its follow-up -----------------------------

def test_a_timed_nudges_reward_is_its_follow_ups_change():
    recs = _turns(BALL, 6, lambda b: 1.0 if b % 2 else 0.0)
    rows = SL._turn_nudges(recs, BALL)
    assert len(rows) == 6
    ups = [r for r in recs if r.get("follow")]
    assert [x["replied"] for x in rows] == [u["answer"] == "changed" for u in ups]
    assert [x["bin"] for x in rows] == [r["bin"] for r in recs if r.get("kind") == "nudge"]


def test_a_timed_nudge_without_its_follow_up_has_no_row():
    recs = _turns(BALL, 3, lambda b: 1.0)
    recs[-1] = {**recs[-1], "answer": "none"}                    # the follow-up got no answer
    t = T0 + 10_000
    recs += [{"ts": t, "kind": "nudge", "eid": BALL, "rc": 0, "bin": 1, "pick": 1, "best": None,
              "dwell": [0, 1, 0, 0]},
             {"ts": t + 1, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 1, "answer": "none"},   # no check
             {"ts": t + 40, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 0, "answer": "changed"}]
    assert len(SL._turn_nudges(recs, BALL)) == 2


def test_an_untimed_nudge_is_no_turn():
    recs = [{"ts": T0, "kind": "nudge", "eid": BALL, "rc": 0},
            {"ts": T0 + 1, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 1, "answer": "changed"},
            {"ts": T0 + 31, "kind": "reach", "eid": BALL, "p": 0.1, "nudged": 0, "follow": True,
             "answer": "changed"}]
    assert SL._turn_nudges(recs, BALL) == []


# ---- the turns' test ------------------------------------------------------------------------

def test_a_turn_taker_passes_over_its_timed_nudges():
    recs = _meet(BALL) + _turns(BALL, 200, _turn_taker)
    p = SL._rung7(recs)
    assert p["passed"], p
    assert p["eid"] == BALL
    assert list(p["crit"]) == ["nudges", "contingency", "payoff"]
    assert p["n"] == SL.RUNG7_NUDGES


def test_a_coin_other_does_not_pass():
    recs = _meet(BALL) + _turns(BALL, 200, lambda b: 0.5)
    p = SL._rung7(recs)
    assert not p["passed"], p
    assert p["crit"]["nudges"] == 1.0


def test_99_timed_nudges_do_not_pass():
    recs = _meet(BALL) + _turns(BALL, 99, _turn_taker)
    p = SL._rung7(recs)
    assert not p["passed"] and p["crit"]["nudges"] < 1.0


def test_only_timed_nudges_at_meets_other_count():
    recs = _meet(DRIFT) + _turns(BALL, 200, _turn_taker)
    p = SL._rung7(recs)
    assert not p["passed"] and p["crit"]["nudges"] == 0.0
    assert not SL._rung7(_turns(BALL, 200, _turn_taker))["passed"]   # no meet pass: no other


def test_the_old_pushes_count_for_nothing():
    old = []
    for i in range(150):
        t = T0 + i * 400.0
        old.append({"ts": t, "kind": "push", "to": BALL, "medium": "toy", "clock": 2, "bin": 1, "aim": 1,
                    "best": 1, "dwell": [1, 1, 0, 0]})
        old.append({"ts": t + 30, "kind": "reach", "eid": BALL, "p": 0.1, "reply": True, "replied": True,
                    "medium": "toy", "clock": 2, "answer": "changed"})
    p = SL._rung7(_meet(BALL) + old)
    assert not p["passed"] and p["crit"]["nudges"] == 0.0
    q = SL._rung8(_meet(BALL) + old)
    assert not q["passed"] and q["crit"]["looks"] == 0.0


# ---- expect's p_self and test -----------------------------------------------------------------

def test_freshness_is_on_the_balls_clock():
    off, X = SL._r8_design([{"o": 1.0, "gap": 60.0}, {"o": 0.0, "gap": None}])
    assert X[0][3] == pytest.approx(math.exp(-1.0))
    assert X[1][3] == 0.0


def test_p_self_is_prequential():
    recs = _expects(BALL, 30, lambda g: 1.0 if g < 30 else 0.0)
    ups = [r for r in recs if r.get("follow")]
    for i in (0, 5, 29):
        cut = recs[:recs.index(ups[i])]
        assert ups[i]["p_self"] == SL._p_follow(cut, BALL, ups[i]["gap"])   # from the follow-ups before only
    assert ups[0]["p_self"] == 0.5                                           # nothing yet: the running rate


def test_an_other_who_answers_fresh_nudges_is_expected():
    recs = _meet(BALL) + _expects(BALL, 160, lambda g: 0.95 if g < 40 else 0.05)
    p = SL._rung8(recs)
    assert p["passed"], p
    assert p["eid"] == BALL and p["crit"] == {"looks": 1.0, "skill": 1.0}
    assert p["skill"] >= SL.RUNG8_MARGIN


def test_a_coin_other_is_not_expected():
    recs = _meet(BALL) + _expects(BALL, 160, lambda g: 0.5)
    p = SL._rung8(recs)
    assert not p["passed"], p
    assert p["crit"]["looks"] == 1.0 and p["skill"] < SL.RUNG8_MARGIN


def test_only_predicted_follow_ups_are_scored_and_all_set_the_rate():
    early = _expects(BALL, 60, lambda g: 1.0, seed=5, predict=False)
    late = _expects(BALL, 99, lambda g: 0.95 if g < 40 else 0.05, t0=T0 + 100_000)
    p = SL._rung8(_meet(BALL) + early + late)
    assert p["crit"]["looks"] == pytest.approx(99 / SL.RUNG8_LOOKS) and not p["passed"]
    rows = SL._follow_series(early + late, BALL)
    assert len(rows) == 159
    assert rows[60]["rate"] == pytest.approx(60.5 / 61.0)                    # the early ones set the running rate


def test_the_advisor_stays_out():
    recs = _meet(BALL) + _expects(BALL, 160, lambda g: 0.5)
    told = [{**r, "p_llm": 0.99 if r.get("answer") == "changed" else 0.01, "llm_model": "m"}
            if r.get("follow") else r for r in recs]
    assert SL._rung8(told) == SL._rung8(recs)


def test_only_follow_ups_at_meets_other_count():
    recs = _meet(DRIFT) + _expects(BALL, 160, lambda g: 0.95 if g < 40 else 0.05)
    p = SL._rung8(recs)
    assert not p["passed"] and p["crit"]["looks"] == 0.0


# ---- the pass lines --------------------------------------------------------------------------

def _record_seat(tmp_path, record):
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=None)
    est = srv.entity_store = EntityStore(path=str(tmp_path / "entities.json"), shared=False)
    srv._models_path = str(tmp_path / "models.json")
    est._srv = srv
    p = SL.record_path(srv)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in record))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


def _head(accept_meet=True):
    out = [{"ts": T0 - 2000, "kind": "birth", "by": "operator"}]
    for i in ("feel", "reach", "nudge"):
        out.append({"ts": T0 - 1900, "kind": "achieve", "id": i, "rung": SL.ACHIEVEMENTS[i]["rung"], "skill": 0.9,
                    "ladder": SL.LADDER_V})
    out.append({"ts": T0 - 1800, "kind": "accept_migrated", "ids": ["feel", "reach", "nudge"]})
    out += _meet(BALL, T0 - 1700)
    if accept_meet:
        out.append({"ts": T0 - 1600, "kind": "accept", "id": "meet", "by": "operator"})
    return out


@pytest.fixture
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    (tmp_path / "data").mkdir()
    yield tmp_path / "data"


def _lines(srv):
    return [json.loads(ln) for ln in SL.record_path(srv).read_text().splitlines() if ln.strip()]


def test_the_pass_lines_carry_ladder_3_and_the_other(_data):
    record = _head() + _turns(BALL, 200, _turn_taker) + \
        _expects(BALL, 160, lambda g: 0.95 if g < 40 else 0.05, t0=T0 + 200_000)
    srv = _record_seat(_data, record)
    SL.set_mode(srv, "on")
    SL._open_rungs(srv)
    got = {r["id"]: r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") in ("turns", "expect")}
    assert set(got) == {"turns", "expect"}
    for i, n in (("turns", 7), ("expect", 8)):
        assert got[i]["ladder"] == 3 and got[i]["rung"] == n and got[i]["eid"] == BALL


def test_no_pass_before_meet_is_accepted(_data):
    record = _head(accept_meet=False) + _turns(BALL, 200, _turn_taker)
    srv = _record_seat(_data, record)
    SL.set_mode(srv, "on")
    SL._open_rungs(srv)
    assert not [r for r in _lines(srv) if r.get("kind") == "achieve" and r.get("id") in ("turns", "expect")]


# ---- the practice on a seat: the timed nudge, the predicted follow-up -------------------------

A, B = NS + "a", NS + "b"


class _Field:
    def __init__(self):
        v = np.random.default_rng(1).normal(size=DIM)
        self.cur = v / np.linalg.norm(v)

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Script:
    def __init__(self):
        self.q = {}

    def __call__(self, srv, ent, now):
        q = self.q.get(ent.eid) or []
        ch = q.pop(0) if q else False
        if ch is None:
            return {"fired": False, "reason": "did not fire"}
        return {"fired": True, "state": {"v": 1}, "changed": bool(ch)}


def _scripted(tmp_path, monkeypatch, isolate=("reach", "nudge", "meet", "turns", "expect"), other=A):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    WA._getter_paused.clear()
    script = _Script()
    monkeypatch.setattr(SL, "_read_entity", script)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(tmp_path / "entities.json"), shared=False)
    srv._models_path = str(tmp_path / "models.json")
    srv.efield = _Field()
    est._srv = srv
    for name in ("a", "b"):
        e = QuantumEntity(eid=NS + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    world(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in (1, 2, 13):
        SL._append(srv, {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9, "ladder": SL.LADDER_V})
    SL._append(srv, {"ts": 20.0, "kind": "achieve", "id": "meet", "rung": 6, "skill": 0.5,
                     "ladder": SL.LADDER_V, "eid": other})
    rt = SL._rt(srv)
    rt["mode"] = "on"
    rt["isolate"] = set(isolate)
    srv.script = script
    return srv


@pytest.fixture
def seat(tmp_path, monkeypatch):
    return _scripted(tmp_path, monkeypatch)


def _looks(srv, eid=None):
    return [r for r in SL._read(srv) if r.get("kind") == "reach" and (eid is None or r["eid"] == eid)]


def _nudges(srv, eid=None):
    return [r for r in SL._read(srv) if r.get("kind") == "nudge" and (eid is None or r["eid"] == eid)]


def _step(srv, eid, now, n=0):
    """The cycle's nudge while closing in on eid (_nudge_step)."""
    return SL._nudge_step(srv, n, now, eid, np.zeros(DIM))


def _reach(srv, now, answers=None):
    for e, ch in (answers or {}).items():
        srv.script.q[e] = [ch]
    n0 = len(_looks(srv))
    SL._reach_once(srv, srv.entity_store, SL._entity(srv), 0, now)
    got = _looks(srv)
    return got[-1] if len(got) > n0 else None


def _only(srv, eid, now, changed):
    others = [e for e in (A, B) if e != eid]
    keep = {e: srv.entity_store.docs.pop(e) for e in others}
    try:
        return _reach(srv, now, {eid: changed})
    finally:
        srv.entity_store.docs.update(keep)


def test_the_age_is_since_its_own_looks_last_saw_the_other_change(seat):
    assert SL._age_at(SL._read(seat), A, T0) is None                          # never seen changed
    _only(seat, A, T0, True)
    _only(seat, A, T0 + 10, False)
    assert SL._age_at(SL._read(seat), A, T0 + 25) == pytest.approx(25.0)
    _step(seat, B, T0 + 26)                                                   # another thing: ungated
    SL._nudge_once(seat, 0, T0 + 30, A, np.zeros(DIM))
    _only(seat, A, T0 + 31, True)                                             # a check: its own nudge's change
    assert SL._age_at(SL._read(seat), A, T0 + 40) == pytest.approx(40.0)


def test_it_nudges_the_other_only_in_the_bin_its_bandit_picks(seat, monkeypatch):
    monkeypatch.setattr(SL, "_turn_choose", lambda rows, rng: (1, None))     # the bandit picks 20-60 s
    _only(seat, A, T0, True)                                                  # it saw the other change
    assert _step(seat, A, T0 + 5) is None                                     # 0-20 s: not its bin
    assert _step(seat, A, T0 + 10) is None
    assert _nudges(seat, A) == []
    line = _step(seat, A, T0 + 30)                                            # 20-60 s: its bin
    assert line is not None and line["kind"] == "nudge" and line["eid"] == A
    assert (line["bin"], line["pick"], line["best"]) == (1, 1, None)
    assert line["dwell"] == [2, 1, 0, 0]                                      # the moments it waited through
    assert line["age"] == pytest.approx(30.0)


def test_a_thing_other_than_meets_other_is_nudged_freely(seat, monkeypatch):
    monkeypatch.setattr(SL, "_turn_choose", lambda rows, rng: (3, None))
    line = _step(seat, B, T0)
    assert line is not None and "bin" not in line and "dwell" not in line


def test_without_turns_practised_the_other_is_nudged_freely(tmp_path, monkeypatch):
    srv = _scripted(tmp_path, monkeypatch, isolate=("reach", "nudge", "meet"))
    monkeypatch.setattr(SL, "_turn_choose", lambda rows, rng: (3, None))
    _only(srv, A, T0, True)
    line = _step(srv, A, T0 + 5)
    assert line is not None and "bin" not in line


def test_the_turn_is_open_until_its_follow_up(seat, monkeypatch):
    monkeypatch.setattr(SL, "_turn_choose", lambda rows, rng: (3, None))     # never seen: the last bin
    assert _step(seat, A, T0) is not None                                     # the timed nudge
    assert _step(seat, A, T0 + 1) is None                                     # open: no check yet
    chk = _only(seat, A, T0 + 2, True)
    assert chk["nudged"] == 1
    assert _step(seat, A, T0 + 3) is None                                     # open: no follow-up yet
    up = _only(seat, A, T0 + 2 + SL.FOLLOW_S, True)
    assert up["follow"] is True
    monkeypatch.setattr(SL, "_turn_choose", lambda rows, rng: (1, None))
    assert _step(seat, A, T0 + 2 + SL.FOLLOW_S + 25) is not None              # closed: its age is 25 s
    rows = SL._turn_nudges(SL._read(seat), A)
    assert [(r["bin"], r["replied"]) for r in rows] == [(3, True)]
    assert len(_nudges(seat, A)) == 2


def test_a_follow_up_at_the_other_carries_its_gap_and_p_self(seat):
    _only(seat, A, T0, True)
    SL._nudge_once(seat, 0, T0 + 50, A, np.zeros(DIM))
    _only(seat, A, T0 + 51, True)                                             # the check
    up = _only(seat, A, T0 + 51 + SL.FOLLOW_S, False)
    assert up["follow"] is True
    assert up["gap"] == pytest.approx(50.0)                                   # the age at its nudge
    assert up["p_self"] == 0.5                                                # no follow-up before: the rate
    assert "p_llm" not in up and "p_mix" not in up                            # the advisor stays out


@pytest.mark.parametrize("isolate,other", [(("reach", "nudge", "meet"), A),              # expect not practised
                                           (("reach", "nudge", "meet", "turns", "expect"), B)])   # not its other
def test_a_follow_up_elsewhere_or_without_expect_carries_no_p_self(tmp_path, monkeypatch, isolate, other):
    srv = _scripted(tmp_path, monkeypatch, isolate=isolate, other=other)
    SL._nudge_once(srv, 0, T0, A, np.zeros(DIM))
    _only(srv, A, T0 + 1, True)
    up = _only(srv, A, T0 + 1 + SL.FOLLOW_S, True)
    assert up["follow"] is True and "p_self" not in up


# ---- what sleep keeps ----------------------------------------------------------------------

def test_the_windows_survive_the_consolidation(monkeypatch):
    monkeypatch.setattr(SL, "TOY_SCAN", 50)                      # the record's last lines, kept whole
    recs = (_meet(BALL) + _turns(BALL, 240, _turn_taker)
            + _expects(BALL, 560, lambda g: 0.95 if g < 40 else 0.05, t0=T0 + 200_000))
    keep = sorted(SL._keep(recs, SL._Index(recs)))
    gone = [i for i in range(len(recs)) if i not in set(keep)]
    assert gone                                                  # something was archived
    head = {"kind": SL.SUMMARY, "id": "x", "at": T0, "n": len(keep),
            "carry": SL._tally(recs, gone, SL._Index(recs))}
    after = [head] + [recs[i] for i in keep]
    for f in (SL._rung7, SL._rung8):
        assert f(after) == f(recs)
    assert SL._p_follow(after, BALL, 30.0) == SL._p_follow(recs, BALL, 30.0)


# ---- the sheet ----------------------------------------------------------------------------

def test_the_sheet_says_the_turns_stages():
    recs = _meet(BALL) + _turns(BALL, 200, _turn_taker)
    crits = {c["key"]: c for c in SS._criteria(7, recs, {7: SL._rung7(recs)})}
    assert list(crits) == ["nudges", "contingency", "payoff"]
    assert crits["nudges"]["text"] == f"{SL.RUNG7_NUDGES}/{SL.RUNG7_NUDGES} timed nudges at its other, followed up"
    assert crits["contingency"]["text"].startswith("nudges in its window")
    assert "timed nudges" in SS._needs()[7]


def test_the_sheet_says_expects_stages():
    recs = _meet(BALL) + _expects(BALL, 160, lambda g: 0.95 if g < 40 else 0.05)
    crits = {c["key"]: c for c in SS._criteria(8, recs, {8: SL._rung8(recs)})}
    assert list(crits) == ["looks", "skill"]
    assert crits["looks"]["text"] == f"{SL.RUNG8_LOOKS}/{SL.RUNG8_LOOKS} follow-ups it predicted"
    assert "follow-ups" in SS._needs()[8]


def test_the_status_numbers_read_the_rebuilt_rungs():
    recs = _meet(BALL) + _turns(BALL, 200, _turn_taker)
    s7 = SL._rung7_stats(recs)
    assert s7["other"] == BALL and s7["nudges"] == 200
    assert [b["bin"] for b in s7["bins"]] == list(SL.TURN_NAMES)
    assert SL.TURN_NAMES == ("0-20s", "20-60s", "1-3m", "3m+")
    assert s7["best"] == "20-60s"
    recs = _meet(BALL) + _expects(BALL, 160, lambda g: 0.95 if g < 40 else 0.05)
    s8 = SL._rung8_stats(recs)
    assert s8["other"] == BALL and s8["checks"] == SL.RUNG8_LOOKS
    assert s8["brier_self"] < s8["brier_rate"]


@pytest.mark.skipif(not __import__("shutil").which("node"), reason="node not installed")
def test_the_card_shows_the_rebuilt_rungs():
    import subprocess
    ui = Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js"
    t7 = SL._rung7_stats(_meet(BALL) + _turns(BALL, 200, _turn_taker))
    t8 = SL._rung8_stats(_meet(BALL) + _expects(BALL, 160, lambda g: 0.95 if g < 40 else 0.05))
    st = {"self": {"mode": "on", "rung": 6, "rung_name": "meet an other", "next": 7, "bar": 0.5, "cycles": 10,
                   "ladder": [], "rung7": t7, "rung8": t8}}
    js = "const L=require(%r); process.stdout.write(JSON.stringify(L.selfCardHtml(%s)))" % (str(ui), json.dumps(st))
    html = json.loads(subprocess.check_output(["node", "-e", js]))
    assert "timed nudges with a follow-up" in html and "nudges in its window" in html
    assert f"<b>{SL.RUNG8_LOOKS}</b> follow-ups" in html and "asked pushes" not in html


def test_a_new_aim_at_the_other_draws_its_moment_again(seat, monkeypatch):
    """A bin that never comes (a still other: its age stays in the last bin)
    does not stall the turns: the next aim at the other draws again; the
    waiting time runs on from the last timed nudge."""
    picks = iter([0, 3])
    monkeypatch.setattr(SL, "_turn_choose", lambda rows, rng: (next(picks), None))
    rt = SL._rt(seat)
    rt["aim_n"] = 1
    assert _step(seat, A, T0) is None and _step(seat, A, T0 + 10) is None      # never seen change: 3m+, not 0-20s
    rt["aim_n"] = 2                                                           # a new aim at it
    line = _step(seat, A, T0 + 20)
    assert line is not None and (line["bin"], line["pick"]) == (3, 3)
    assert line["dwell"] == [0, 0, 0, 3]
