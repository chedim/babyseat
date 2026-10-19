"""#161: the rung ruler -- excess entropy E and statistical complexity C_mu
of the processes self's rungs ask it to predict (experiments/rung_ruler/).

Only the estimators are pinned here, on processes whose answers are known in
closed form; the numbers on self's record come from a private copy of the
live record and live in the experiment's README.
"""
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments" / "rung_ruler"))

import ruler as R  # noqa: E402

H23 = -(2 / 3) * math.log2(2 / 3) - (1 / 3) * math.log2(1 / 3)   # 0.9183 bits


def coin(n, p=0.5, seed=0):
    rng = random.Random(seed)
    return [1 if rng.random() < p else 0 for _ in range(n)]


def period2(n):
    return [i % 2 for i in range(n)]


def golden_mean(n, seed=0):
    """No two 0s in a row: after a 0 always 1; after a 1, a fair coin.
    h_mu = 2/3 bit, C_mu = H(2/3, 1/3) = 0.918 bit, E = C_mu - h_mu = 0.252 bit."""
    rng = random.Random(seed)
    x, out = 1, []
    for _ in range(n):
        x = 1 if x == 0 else (1 if rng.random() < 0.5 else 0)
        out.append(x)
    return out


# ---------------------------------------------------------------- special functions

def test_digamma_matches_known_values():
    gamma = 0.5772156649015329
    assert abs(R.digamma(1) + gamma) < 1e-9
    assert abs(R.digamma(0.5) + gamma + 2 * math.log(2)) < 1e-9
    assert abs(R.digamma(10) - (sum(1 / k for k in range(1, 10)) - gamma)) < 1e-9


def test_chi2_sf_matches_known_values():
    assert abs(R.chi2_sf(3.841458820694124, 1) - 0.05) < 1e-6
    assert abs(R.chi2_sf(5.991464547107979, 2) - 0.05) < 1e-6
    assert abs(R.chi2_sf(0.0, 3) - 1.0) < 1e-12


# ---------------------------------------------------------------- block entropies

def test_block_entropy_plugin_on_exact_blocks():
    assert abs(R.block_entropy(period2(1000), 1, "plugin") - 1.0) < 1e-3
    assert abs(R.block_entropy(period2(1000), 3, "plugin") - 1.0) < 1e-3
    assert R.block_entropy([0] * 50, 2, "plugin") == 0.0


def test_corrections_raise_a_small_sample_plugin_estimate():
    x = coin(60, seed=3)
    pl = R.block_entropy(x, 4, "plugin")
    assert R.block_entropy(x, 4, "mm") > pl
    assert R.block_entropy(x, 4, "grassberger") > pl


def test_lmax_keeps_blocks_well_sampled():
    assert R.choose_lmax(77, 2) == 2          # 2^3 = 8 > 77/10
    assert R.choose_lmax(100, 2) == 3
    assert R.choose_lmax(14000, 2) >= 8
    assert R.choose_lmax(6000, 3) >= 5
    assert R.choose_lmax(5, 2) == 1


# ---------------------------------------------------------------- E and h_mu

def test_fair_coin_has_no_excess_entropy():
    r = R.excess_entropy(coin(20000, seed=1), lmax=6)
    assert abs(r["E"]) < 0.02
    assert abs(r["h_mu"] - 1.0) < 0.02


def test_period_two_has_one_bit_of_excess_entropy():
    r = R.excess_entropy(period2(5000), lmax=5)
    assert abs(r["E"] - 1.0) < 0.01
    assert abs(r["h_mu"]) < 0.01


def test_golden_mean_has_known_entropy_rate_and_e():
    r = R.excess_entropy(golden_mean(50000, seed=2), lmax=6)
    assert abs(r["h_mu"] - 2 / 3) < 0.02
    assert abs(r["E"] - (H23 - 2 / 3)) < 0.03


def test_lmax_two_is_lag_one_mutual_information():
    x = golden_mean(20000, seed=5)
    r = R.excess_entropy(x, lmax=2, correction="plugin")
    h1 = R.block_entropy(x, 1, "plugin")
    h2 = R.block_entropy(x, 2, "plugin")
    assert abs(r["E"] - (2 * h1 - h2)) < 1e-9


def test_shuffle_null_sits_near_zero_and_structure_beats_it():
    x = golden_mean(3000, seed=7)
    null = R.shuffle_null(x, lmax=3, reps=50, seed=0)
    assert abs(null["mean"]) < 0.02
    assert R.excess_entropy(x, lmax=3)["E"] > null["q95"] + 0.1


def test_block_bootstrap_ci_brackets_the_estimate():
    x = golden_mean(4000, seed=9)
    est = R.excess_entropy(x, lmax=4)["E"]
    lo, hi = R.block_bootstrap_ci(x, lmax=4, reps=200, seed=0)
    assert lo < est < hi
    assert lo > 0.1                            # real structure: the CI clears 0


def test_coin_bootstrap_ci_contains_zero():
    lo, hi = R.block_bootstrap_ci(coin(500, seed=4), lmax=3, reps=200, seed=0)
    assert lo <= 0.0 <= hi or hi < 0.05


# ---------------------------------------------------------------- epsilon-machine (CSSR)

def test_cssr_coin_is_one_state():
    m = R.cssr(coin(20000, seed=11), lmax=4)
    assert m["states"] == 1
    assert m["C_mu"] < 0.01


def test_cssr_period_two_is_two_states_one_bit():
    m = R.cssr(period2(2000), lmax=3)
    assert m["states"] == 2
    assert abs(m["C_mu"] - 1.0) < 0.01


def test_cssr_golden_mean_is_two_states():
    m = R.cssr(golden_mean(50000, seed=12), lmax=4)
    assert m["states"] == 2
    assert abs(m["C_mu"] - H23) < 0.02


def test_cssr_period_three_is_three_states():
    m = R.cssr([(i % 3 == 0) * 1 for i in range(3000)], lmax=4)
    assert m["states"] == 3
    assert abs(m["C_mu"] - math.log2(3)) < 0.01


# ---------------------------------------------------------------- symbolization

def test_symbols_of_answers():
    assert R.binary_answers(["changed", "unchanged", "none", "changed"]) == [1, 0, 1]
    assert R.ternary_answers(["changed", "unchanged", "none"]) == [1, 0, 2]


def test_median_split():
    assert R.median_split([0.1, 0.5, 0.2, 0.9]) == [0, 1, 0, 1]


def test_cssr_lmax_is_shorter_than_the_entropy_lmax():
    # CSSR's longest histories need many more samples than a block entropy:
    # one false split at the longest length cascades through determinization
    assert R.cssr_lmax(1249, 2) == 4
    assert R.cssr_lmax(99, 2) == 1
    assert R.cssr_lmax(14341, 2) == 8


def test_cssr_biased_coin_stays_one_state_at_small_n():
    for seed in range(10):
        x = coin(1250, p=0.7, seed=100 + seed)
        assert R.cssr(x, R.cssr_lmax(len(x), 2))["states"] == 1


# ---------------------------------------------------------------- the Brier ceiling

def test_prequential_brier_golden_mean():
    x = golden_mean(20000, seed=21)
    assert abs(R.prequential_brier(x, 0) - 2 / 9) < 0.01          # rate only: p(1-p), p = 2/3
    assert abs(R.prequential_brier(x, 1) - 1 / 6) < 0.01          # 2/3 of the time a fair coin, else sure


def test_prequential_brier_coin_order_does_not_help():
    x = coin(5000, seed=22)
    assert abs(R.prequential_brier(x, 0) - 0.25) < 0.01
    assert R.prequential_brier(x, 3) >= R.prequential_brier(x, 0) - 0.002
