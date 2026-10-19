"""The petri-dish lab (experiments/petri_dish, #161): the dish's MECHANICS only.

A dish is a population of predictor variants; its prediction is the
copy-weighted mean (mix) or one variant drawn in proportion to its copies
(sample); after each outcome a variant that beat the dish multiplies, one that
did worse is consumed, never under FLOOR copies, the total never over CAP.
These tests keep the lab code trustworthy -- they say nothing about whether
the dish predicts well (that is the experiment's README)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "petri_dish"))

import dish as D  # noqa: E402


class Const:
    """A variant that always says p."""

    def __init__(self, p, name=None):
        self.p, self.name = p, name or f"c{p}"

    def predict(self, dt):
        return self.p

    def update(self, dt, y):
        pass


def test_mixture_is_the_copy_weighted_mean():
    d = D.Dish([Const(0.2), Const(0.8)], init=1)
    d.copies = [3, 1]
    assert d.predict(10.0) == pytest.approx((3 * 0.2 + 1 * 0.8) / 4)


def test_equal_copies_is_the_plain_mean():
    d = D.Dish([Const(0.1), Const(0.5), Const(0.9)], init=7)
    assert d.predict(1.0) == pytest.approx(0.5)


def test_better_than_the_dish_multiplies_worse_is_consumed():
    d = D.Dish([Const(0.9), Const(0.1)], init=10, grow=0.5)
    d.predict(1.0)
    d.update(1.0, 1)                       # it changed: 0.9 beat the dish (0.5), 0.1 lost
    assert d.copies[0] > 10 and d.copies[1] < 10


def test_a_tie_with_the_dish_keeps_its_copies():
    d = D.Dish([Const(0.5), Const(0.5)], init=4)
    d.predict(1.0)
    d.update(1.0, 0)
    assert d.copies == [4, 4]


def test_floor_nothing_goes_extinct():
    d = D.Dish([Const(0.99), Const(0.01)], init=10, grow=0.5)
    for _ in range(200):
        d.predict(1.0)
        d.update(1.0, 1)
    assert min(d.copies) == D.FLOOR == 1
    assert d.predict(1.0) < 0.99           # the floor copy still votes (exploration)


def test_cap_holds_the_population():
    d = D.Dish([Const(0.99), Const(0.6), Const(0.01)], init=10, grow=0.5, cap=50)
    for _ in range(500):
        d.predict(1.0)
        d.update(1.0, 1)
        assert sum(d.copies) <= 50
        assert min(d.copies) >= 1
    assert d.copies[0] == max(d.copies)


def test_sampled_dish_replays_under_a_seed():
    def run(seed):
        d = D.Dish([Const(0.1), Const(0.5), Const(0.9)], init=5, mode="sample", seed=seed)
        out = []
        for i in range(100):
            out.append(d.predict(1.0))
            d.update(1.0, i % 3 == 0)
        return out
    assert run(7) == run(7)
    assert run(7) != run(8)
    assert set(run(7)) <= {0.1, 0.5, 0.9}


def test_sampled_draws_follow_the_copies():
    d = D.Dish([Const(0.0), Const(1.0)], init=1, mode="sample", seed=1)
    d.copies = [1, 3]
    draws = [d.predict(1.0) for _ in range(4000)]
    assert sum(draws) / len(draws) == pytest.approx(0.75, abs=0.03)


def test_hedge_and_static_mixture():
    s = D.StaticMix([Const(0.2), Const(0.6)])
    assert s.predict(1.0) == pytest.approx(0.4)
    h = D.Hedge([Const(0.9), Const(0.1)], eta=2.0)
    assert h.predict(1.0) == pytest.approx(0.5)
    h.update(1.0, 1)
    assert h.predict(1.0) > 0.5            # the variant that was right gains weight


def test_variants_learn_without_lookahead():
    v = D.OneBit()
    assert v.predict(None) == 0.5
    for y in (1, 0, 1, 0, 1, 0):
        v.update(10.0, y)
    assert v.predict(10.0) > 0.5           # last was 0, after a 0 came a 1 every time
    r = D.Rate(window=30)
    for _ in range(20):
        r.update(10.0, 1)
    assert r.predict(10.0) > r.predict(0.1)  # longer wait, likelier change


def test_dish_run_is_deterministic():
    seq = [(10.0, i % 4 != 0) for i in range(60)]
    a = D.run(lambda: D.Dish(D.make_variants(), mode="sample", seed=3), seq)
    b = D.run(lambda: D.Dish(D.make_variants(), mode="sample", seed=3), seq)
    assert a == b and len(a) == 60
