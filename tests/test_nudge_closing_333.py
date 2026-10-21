"""#333, the operator's decision 1: Blurp nudges only while it is getting
CLOSER to its target -- its distance to the aimed thing fell since its
previous cycle on the same aim. Otherwise it does not nudge that cycle, so a
ball sitting on the person's side no longer drains the error fee every cycle.
A new aim's first cycle has no previous distance: no nudge. Strictly "fell":
no margin, no new constant.

The thing's field spot is faked here so the test sets the distance each
cycle: the spot sits a set angle away from where Blurp stands after its move."""
import numpy as np
import pytest

from membrane import energy as EN
from membrane import self_loop as SL
from membrane import toybox as TB

from tests.test_nudge_333 import DIM, T0, _cid, _felt, _lines  # noqa: F401  (fixtures below)
from tests.test_nudge_333 import _data  # noqa: F401  autouse fixture

BALL = _cid("toy:ball")


def _fake_spots(monkeypatch, sched):
    """The ball's spot: sched[step] (an angle) away from Blurp's own
    (post-move) position, so its distance is exactly 2 sin(angle / 2) whatever
    Blurp's move; `step` is the cycle the test is running."""
    state = {"step": 0}
    ortho = np.zeros(DIM)
    ortho[0] = 1.0

    def spot(srv, eid):
        from membrane.eids import field_id
        if field_id(eid) != field_id(BALL):
            return None
        me = SL._entity(srv)
        pos = np.asarray(me.position, dtype=float) if me.position is not None else np.ones(DIM)
        pos = SL._unit(pos)
        o = ortho - float(ortho @ pos) * pos                     # a direction square to where it stands
        o = o / np.linalg.norm(o)
        th = sched[min(state["step"], len(sched) - 1)]           # the angle: the distance is 2 sin(th / 2)
        return np.cos(th) * pos + np.sin(th) * o

    monkeypatch.setattr(SL, "_spot", spot)
    return state


def _run(srv, state, k_cycles):
    for k in range(k_cycles):
        state["step"] = k
        SL.cycle(srv, now=T0 + 100 + 11 * k)


def _nudges(srv):
    return [r for r in _lines(srv) if r.get("kind") == "nudge"]


def _fees(srv):
    return [r for r in _lines(srv) if r.get("kind") == "error" and r.get("cost")]


def test_closing_in_it_nudges_every_cycle_but_the_aims_first(monkeypatch):
    srv = _felt(toys=("ball",))
    state = _fake_spots(monkeypatch, [0.5 - 0.01 * k for k in range(10)])
    _run(srv, state, 6)
    cyc = [r for r in _lines(srv) if r.get("kind") == "cycle"]
    ns = _nudges(srv)
    assert len(cyc) == 6
    assert [r["n"] for r in ns] == [c["n"] for c in cyc[1:]]     # the aim's first cycle: none


def test_not_closing_in_it_does_not_nudge_and_pays_no_fee(monkeypatch):
    srv = _felt(toys=("ball",))
    TB.nudge("toy:ball", now=T0)                                 # the ball sits on the person's side
    state = _fake_spots(monkeypatch, [0.2 + 0.01 * k for k in range(10)])   # moving away
    e0 = EN.level(srv)
    _run(srv, state, 6)
    assert not _nudges(srv)
    assert not _fees(srv)
    assert EN.level(srv) == pytest.approx(e0)


def test_holding_still_is_not_closing_in(monkeypatch):
    srv = _felt(toys=("ball",))
    state = _fake_spots(monkeypatch, [0.3] * 6)                  # the same distance: it did not fall
    _run(srv, state, 6)
    assert not _nudges(srv)


def test_a_new_aims_first_cycle_does_not_nudge(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 3)
    srv = _felt(toys=("ball",))
    state = _fake_spots(monkeypatch, [0.9 - 0.02 * k for k in range(12)])  # closing in all along
    _run(srv, state, 9)
    cyc = [r for r in _lines(srv) if r.get("kind") == "cycle"]
    firsts = {c["n"] for c in cyc if c["n"] == c["aim_n"]}
    assert len(firsts) == 3                                      # three aims, each its own first cycle
    nudged = {r["n"] for r in _nudges(srv)}
    assert not (nudged & firsts)
    assert nudged == {c["n"] for c in cyc} - firsts
    for r in _nudges(srv):
        assert r["aim"] == next(c["aim_n"] for c in cyc if c["n"] == r["n"])


def test_a_ball_on_their_side_over_29_held_cycles_costs_only_the_closing_cycles(monkeypatch):
    srv = _felt(toys=("ball",))
    TB.nudge("toy:ball", now=T0)                                 # on the person's side, and it stays there
    sched = [0.5 + (0.05 if k % 3 == 0 else -0.01 * k) for k in range(30)]   # some closer, some not
    state = _fake_spots(monkeypatch, sched)
    e0 = EN.level(srv)
    _run(srv, state, 30)                                         # the aim's first cycle and 29 held ones
    closing = sum(1 for k in range(1, 30) if sched[k] < sched[k - 1])
    assert 0 < closing < 29
    fees = _fees(srv)
    assert len(_nudges(srv)) == closing
    assert len(fees) <= closing
    assert TB.peek("toy:ball")["side"] == "other"
    assert EN.level(srv) == pytest.approx(e0 - EN.ERROR_COST * len(fees))
