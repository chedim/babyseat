"""#266: attention -- patience ranks due things by what a look can teach, and a
skip that spans a record gap is not a shift.

1. Rung 4 makes a thing DUE once a change has become likely; among the due,
   self used to pick by the curiosity tilt alone. A thing self has mastered (a
   heartbeat: its rate fit runs to the grid's edge, p ~ 1 at every look) is due
   at nearly every cycle and took ~30% of the live seat's looks, though a look
   at p ~ 1 teaches nothing. Now each due thing is weighted by the information
   of the look -- the binary entropy of the p self would predict for it -- with
   a floor (INFO_FLOOR) so a mastered thing is still re-checked now and then,
   times the curiosity tilt. What counts as a timed look, and rung 4's test,
   are unchanged.

2. Rung 5's noticing reads its last few looks against its long memory; a look
   whose interval spans a gap in the record longer than CURIO_GAP_S (a restart
   or a stall, #259's detector) is the glitch's, so a window holding one is no
   evidence of a shift and triggers no rate refit.
"""
import json
import math
import os
import random
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
NS = MEMBRANE_NS + "Component:"


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Clock:
    """Things that change on their own at a true rate in the fake clock's time;
    `beat` changes between any two looks (a heartbeat faster than the pace)."""
    def __init__(self, rates, seed=7):
        self.rates, self.rng, self.last = rates, random.Random(seed), {}

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > time.time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        lam = self.rates[name]
        prev = self.last.get(name)
        self.last[name] = now
        if prev is None:
            return {"fired": True, "state": {"v": 0}, "changed": True}
        changed = self.rng.random() < 1.0 - math.exp(-lam * (now - prev))
        return {"fired": True, "state": {"v": 1}, "changed": changed}


@pytest.fixture(autouse=True)
def _clean():
    WA._getter_paused.clear()


def _seat(names, limit=4, passed=(1, 2, 3)):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    v = np.random.default_rng(1).normal(size=DIM)
    srv.efield = _World(v / np.linalg.norm(v))
    est._srv = srv
    for name in names:
        e = QuantumEntity(eid=NS + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    SL.bind(srv)
    SL.set_limit(srv, 1)
    for r in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    SL.set_limit(srv, limit)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _looks(eid, answers, dt, ps, t0=0.0):
    """Answered reach records at one thing, `dt` apart, with the given answers and p's."""
    return [{"ts": t0 + i * dt, "kind": "reach", "eid": eid, "answer": a, "p": p, "dt": dt}
            for i, (a, p) in enumerate(zip(answers, ps))]


def _picks(srv, recs, targets, now, n=400):
    return [SL._due(srv, recs, targets, {}, now, random.Random(i))[0] for i in range(n)]


# ---- 1. what a look can teach --------------------------------------------------

def test_a_look_at_a_near_certain_thing_teaches_almost_nothing():
    assert SL._info(0.5) == pytest.approx(1.0)
    assert SL._info(0.99995) < 0.01 and SL._info(0.0) == 0.0 and SL._info(1.0) == 0.0
    assert SL._info(0.6) > 0.9


def test_a_mastered_always_changing_thing_loses_the_pick_but_keeps_a_floor():
    """A heartbeat (always changed at the pace: rate at the grid's edge, p ~ 1)
    and an uncertain thing (half its looks find news) are both due: the
    uncertain one wins most picks, the mastered one is still re-checked."""
    M, U = NS + "mastered", NS + "uncertain"
    recs = (_looks(M, ["changed"] * 40, 10.0, [1.0] * 40)
            + _looks(U, ["changed", "unchanged"] * 20, 20.0, [0.5] * 40))
    srv = _seat(("mastered", "uncertain"))
    now = 5000.0
    SL._tempo(srv).update({M: {"last": now - 10.0}, U: {"last": now - 40.0}})
    assert SL._lam(recs, M) == pytest.approx(SL.LAM_GRID[-1])        # mastered: p ~ 1 at any look
    picks = _picks(srv, recs, [M, U], now)
    assert set(picks) == {M, U}                                        # both due
    share = picks.count(M) / len(picks)
    assert share < 0.25                                                # before #266: ~0.5
    assert share > 0.03                                                # the floor: still re-checked


def test_without_attention_the_pick_is_the_tilt_alone(monkeypatch):
    M, U = NS + "mastered", NS + "uncertain"
    recs = (_looks(M, ["changed"] * 40, 10.0, [1.0] * 40)
            + _looks(U, ["changed", "unchanged"] * 20, 20.0, [0.5] * 40))
    srv = _seat(("mastered", "uncertain"))
    now = 5000.0
    SL._tempo(srv).update({M: {"last": now - 10.0}, U: {"last": now - 40.0}})
    monkeypatch.setattr(SL, "ATTENTION", False)
    share = _picks(srv, recs, [M, U], now).count(M) / 400
    assert 0.4 < share < 0.6


def test_the_curiosity_tilt_still_applies_among_equally_uncertain_things():
    """Two due things with the same rate (same p now, same information); one
    is being learned (its error falls), the other is flat: the tilt still
    sends self to the learnable one."""
    W = SL.CURIO_WINDOW
    L, N = NS + "learnable", NS + "flat"
    ans = ["changed", "unchanged"] * W
    p_learn = [0.5] * W + [0.9 if a == "changed" else 0.1 for a in ans[W:]]
    recs = _looks(L, ans, 20.0, p_learn) + _looks(N, ans, 20.0, [0.5] * (2 * W))
    srv = _seat(("learnable", "flat"))
    now = 5000.0
    SL._tempo(srv).update({L: {"last": now - 40.0}, N: {"last": now - 40.0}})
    assert SL._lam(recs, L) == SL._lam(recs, N)
    picks = _picks(srv, recs, [L, N], now)
    assert picks.count(L) / len(picks) > 0.75
    assert picks.count(N) > 0


def test_attention_never_makes_a_thing_due():
    """Patience still decides WHEN: a thing whose change isn't likely yet is
    not picked, however uncertain it is."""
    M, S = NS + "mastered", NS + "slow"
    recs = (_looks(M, ["changed"] * 40, 10.0, [1.0] * 40)
            + _looks(S, ["changed", "unchanged"] * 20, 200.0, [0.5] * 40))
    srv = _seat(("mastered", "slow"))
    now = 50000.0
    SL._tempo(srv).update({M: {"last": now - 10.0}, S: {"last": now - 10.0}})
    assert set(_picks(srv, recs, [M, S], now, n=100)) == {M}


RATES = {"beat": 5.0, "a": 0.1, "b": 0.07, "c": 0.05}   # a heartbeat among things often due together


def _rung4_seat(monkeypatch, attention):
    monkeypatch.setattr(SL, "ATTENTION", attention)
    monkeypatch.setattr(SL, "_read_entity", _Clock(RATES))
    srv = _seat(tuple(RATES), limit=3)
    t = _run(srv, 200)
    SL.set_limit(srv, 4)
    _run(srv, 900, t0=t)
    recs = _record(srv)
    timed = [r for r in recs if r["kind"] == "reach" and r.get("timed")]
    return recs, timed


def test_rung_four_still_passes_and_stays_calibrated_on_a_seat_with_a_heartbeat(monkeypatch):
    """The grade reads predictions over timed looks; attention changes which
    due thing is looked at, not what a timed look is. On a synthetic seat
    with a heartbeat, rung 4's test passes with and without it, and the
    heartbeat's share of the timed looks falls."""
    out = {}
    for attention in (False, True):
        recs, timed = _rung4_seat(monkeypatch, attention)
        g = SL._rung4(recs)
        assert g["crit"]["calibration"] == 1.0, (attention, g)
        assert g["passed"], (attention, g)
        assert 4 in [r["rung"] for r in recs if r["kind"] == "rung"]
        out[attention] = sum(r["eid"] == NS + "beat" for r in timed) / len(timed)
    assert out[True] < 0.6 * out[False]                                # measured: 0.22 -> 0.09


# ---- 2. a skip that spans a record gap is not a shift ------------------------------

def _heartbeat(srv, eid, tail, gap_at=None, n=30, t0=1000.0):
    """n changed looks at a heartbeat then `tail` (answers), PACE_S apart, with
    a record gap of CURIO_GAP_S + 1 s before the tail's look gap_at; the
    long memory (tempo) holds them all. Runs the shift check after each tail
    look like the live loop does."""
    t = t0
    tempo = SL._tempo(srv).setdefault(eid, {"changed": 0, "answered": 0})
    for i, a in enumerate(["changed"] * n + list(tail)):
        if gap_at is not None and i == n + gap_at:
            t += SL.CURIO_GAP_S + 1.0
        t += SL.PACE_S
        SL._append(srv, {"ts": t, "kind": "cycle", "n": i})
        SL._append(srv, {"ts": t, "kind": "reach", "n": i, "eid": eid, "answer": a,
                         "p": 0.99, "dt": SL.PACE_S, "watch5": True})
        tempo["answered"] += 1
        tempo["changed"] += int(a == "changed")
        tempo["last"] = t
        if i >= n:
            SL._shift_check(srv, eid, t)
    return t


def test_a_skip_spanning_a_record_gap_is_no_shift_and_no_refit():
    srv = _seat(("i",), limit=5, passed=(1, 2, 3, 4))
    I = NS + "i"
    _heartbeat(srv, I, ["unchanged", "unchanged", "changed", "changed"], gap_at=0)
    recs = _record(srv)
    assert not [r for r in recs if r["kind"] == "shift"]
    assert SL._last_shift_ts(recs, I) is None                         # the rate fit reads all its looks


def test_the_gap_may_fall_while_self_looks_at_other_things():
    srv = _seat(("i", "x"), limit=5, passed=(1, 2, 3, 4))
    I = NS + "i"
    t = _heartbeat(srv, I, [])
    t += SL.CURIO_GAP_S + 1.0
    SL._append(srv, {"ts": t, "kind": "reach", "eid": NS + "x", "answer": "changed", "p": 0.5})
    _heartbeat(srv, I, ["unchanged", "unchanged", "changed"], n=0, t0=t)
    assert not [r for r in _record(srv) if r["kind"] == "shift"]


def test_a_real_stall_free_change_in_rate_still_shifts():
    srv = _seat(("i",), limit=5, passed=(1, 2, 3, 4))
    I = NS + "i"
    _heartbeat(srv, I, ["unchanged"] * 3)
    shifts = [r for r in _record(srv) if r["kind"] == "shift"]
    assert len(shifts) == 1 and shifts[0]["now"] < shifts[0]["was"]


def test_a_stall_that_outlasts_the_gap_is_still_noticed():
    """The gap only voids the windows that hold its look: a thing that stays
    still after the restart is flagged once the window has moved past it."""
    srv = _seat(("i",), limit=5, passed=(1, 2, 3, 4))
    I = NS + "i"
    _heartbeat(srv, I, ["unchanged"] * 6, gap_at=0)
    shifts = [r for r in _record(srv) if r["kind"] == "shift"]
    assert len(shifts) == 1 and shifts[0]["now"] < shifts[0]["was"]


def test_gap_detection_is_shared_with_curiosity():
    recs = [{"ts": 10.0, "kind": "cycle"}, {"ts": 20.0, "kind": "cycle"},
            {"ts": 20.0 + SL.CURIO_GAP_S + 1.0, "kind": "cycle"}, {"kind": "rung"},
            {"ts": 30.0 + SL.CURIO_GAP_S, "kind": "cycle"}]
    assert SL._stalls(recs) == [0, 0, 1, 1, 1]
