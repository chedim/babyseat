"""#235: after a rung-5 shift, self's rate fit (rung 4's _lam) does not restart
from nothing. The handful of looks since the shift is read against a prior --
the thing's own pre-shift rate (or, without one, the seat's), weighted as a few
pseudo-looks -- so lambda can't run to LAM_GRID's edge (p ~ 0 or 1, patience
waiting the full 30 min) on 3-5 looks, and the new data takes over as it
accumulates. Without a shift nothing changes.
"""
import os
import sys
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402

EID = "urn:membrane:Component:thing"
OTHER = "urn:membrane:Component:other"
DT = 60.0


def _look(ts, y, eid=EID, dt=DT, **kw):
    return {"ts": float(ts), "kind": "reach", "eid": eid, "p": 0.5,
            "answer": "changed" if y else "unchanged", "dt": dt, **kw}


def _mle(obs):
    """The plain grid MLE, as rung 4 has always fitted it."""
    dt = np.array([o[0] for o in obs])[None, :]
    ch = np.array([o[1] for o in obs], dtype=float)[None, :]
    p = np.clip(1.0 - np.exp(-SL.LAM_GRID[:, None] * dt), 1e-9, 1 - 1e-9)
    ll = (ch * np.log(p) + (1 - ch) * np.log(1 - p)).sum(axis=1)
    return float(SL.LAM_GRID[int(np.argmax(ll))])


def _shifted(pre, post, eid=EID):
    """pre / post: lists of 0/1 outcomes around one recorded shift."""
    recs, t = [], 0.0
    for y in pre:
        t += DT
        recs.append(_look(t, y, eid))
    t += 1.0
    recs.append({"ts": t, "kind": "shift", "eid": eid, "was": 0.9, "now": 0.0, "looks": 3})
    for y in post:
        t += DT
        recs.append(_look(t, y, eid))
    return recs


EDGES = (float(SL.LAM_GRID[0]), float(SL.LAM_GRID[-1]))


def test_three_quiet_looks_after_a_shift_do_not_pin_lambda_to_the_grid_edge():
    # a mover (every look changed) goes still: 3 unchanged looks since the shift.
    # From those alone the MLE is 1e-5/s -- p ~ 0, patience waits the full 1800 s.
    recs = _shifted([1, 1, 0, 1, 1, 1, 0, 1, 1, 1] * 3, [0, 0, 0])
    assert _mle([(DT, 0)] * 3) == EDGES[0]                 # the old reset, for the record
    lam = SL._lam(recs, EID)
    assert EDGES[0] < lam < EDGES[1]
    p = 1.0 - np.exp(-lam * DT)
    assert 0.05 < p < 0.95
    # ...and it is pulled toward the new world: below the pre-shift rate
    pre = _mle([(DT, y) for y in [1, 1, 0, 1, 1, 1, 0, 1, 1, 1] * 3])
    assert lam < pre
    # patience no longer waits its cap on the strength of three looks
    assert -np.log(1.0 - SL.PATIENCE_P) / lam < SL.PATIENCE_MAX_S


def test_three_busy_looks_after_a_still_spell_do_not_send_p_to_one():
    recs = _shifted([0, 0, 0, 0, 1, 0, 0, 0, 0, 0] * 3, [1, 1, 1])
    assert 1.0 - np.exp(-_mle([(DT, 1)] * 3) * DT) > 0.99   # the old reset: p ~ 1
    lam = SL._lam(recs, EID)
    assert EDGES[0] < lam < EDGES[1]
    assert 0.05 < 1.0 - np.exp(-lam * DT) < 0.95


def test_without_its_own_history_the_prior_is_the_seats_rate():
    # the thing's pre-shift looks carried no dt (nothing to fit), so the
    # prior comes from the other things' looks
    recs = [_look(i * DT, i % 2, OTHER) for i in range(1, 31)]
    recs += [_look(2000 + i, 1, EID, dt=None) for i in range(5)]
    recs.append({"ts": 3000.0, "kind": "shift", "eid": EID, "was": 0.9, "now": 0.0, "looks": 3})
    recs += [_look(3000 + i * DT, 0, EID) for i in range(1, 4)]
    lam = SL._lam(recs, EID)
    assert EDGES[0] < lam < EDGES[1]


def test_with_ten_or_more_looks_since_the_shift_the_new_data_leads():
    post = [0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0] * 3           # 1 in 6 changed
    for n in (10, 12, 20, 30):
        mine = post[:n]
        new = _mle([(DT, y) for y in mine])
        # a pre-shift world that changed half the time: within ~2 grid steps of the unshrunk fit
        lam = SL._lam(_shifted([1, 0] * 15, mine), EID)
        assert abs(np.log10(lam / new)) < 0.2
        # an extreme one (every look changed) still pulls, but the new looks lead:
        # in log rate the fit sits far nearer the new data than the old world
        old = _mle([(DT, 1)] * 30)
        lam = SL._lam(_shifted([1] * 30, mine), EID)
        assert abs(np.log10(lam / new)) < 0.25 * abs(np.log10(lam / old))


def test_no_shift_no_change():
    for ys in ([0, 0, 0], [1, 1, 1], [1, 0, 1, 1, 0, 0, 1, 0] * 5):
        recs = [_look((i + 1) * DT, y) for i, y in enumerate(ys)]
        obs = [(DT, y) for y in ys][-SL.LAM_LOOKS:]
        assert SL._lam(recs, EID) == _mle(obs)
    assert SL._lam([], EID) is None


def test_no_looks_since_the_shift_is_still_none():
    recs = _shifted([1, 0] * 10, [])
    assert SL._lam(recs, EID) is None


def test_deterministic():
    recs = _shifted([1, 1, 0, 1] * 8, [0, 0, 0, 1])
    assert SL._lam(recs, EID) == SL._lam(list(recs), EID) == SL._lam(recs, EID)
