"""#291: potential symptoms of memory bandwidth imbalance, a toy (experiments/open_loops/sim.py) -- a tracker that
closes an episode only when it recognizes its end, open episodes holding
arousal up against rest pressure, holding costing capacity more the longer
it lasts, and two gentle helps (writing it down, clear endings)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _sim():
    spec = importlib.util.spec_from_file_location("open_loops_sim_291", ROOT / "experiments" / "open_loops" / "sim.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m                 # its dataclass looks itself up there
    spec.loader.exec_module(m)
    return m


def _mean(runs, key):
    xs = [d[key] for r in runs for d in r["days"]]
    return sum(xs) / len(xs)


def _runs(S, theta, seeds=20, days=7, **kw):
    return [S.simulate(theta, days=days, seed=s, **kw) for s in range(seeds)]


def test_same_seed_same_run():
    S = _sim()
    assert S.simulate(0.5, days=5, seed=3) == S.simulate(0.5, days=5, seed=3)


def test_every_ending_recognized_leaves_nothing_open_and_sleep_comes_at_bedtime():
    S = _sim()
    for r in _runs(S, 0.0, seeds=5):
        for d in r["days"]:
            assert d["open_at_bed"] == 0
            assert d["onset_min"] <= 1          # asleep at bedtime: no loop holds it off
            assert d["rest_arrived"] == 0


def test_a_higher_closure_threshold_keeps_more_open_and_delays_sleep():
    S = _sim()
    lo, hi = _runs(S, 0.2), _runs(S, 0.7)
    assert _mean(hi, "open_at_bed") > _mean(lo, "open_at_bed")
    assert _mean(hi, "onset_min") > _mean(lo, "onset_min")
    assert _mean(hi, "settled_at_end_share") < _mean(lo, "settled_at_end_share")


def test_holding_costs_more_the_longer_it_lasts():
    S = _sim()
    st = S.State()
    st.open = [0.0, 0.0, 0.0]
    st.pressure = S.P_BED + 1.0
    a = S.hold_cost(st)
    st.pressure = S.P_BED + 3.0
    b = S.hold_cost(st)
    assert 0 < a < b
    st.pressure = S.P_BED - 1.0                # before rest would have come: holding is free
    assert S.hold_cost(st) == 0.0
    st.open, st.pressure = [], S.P_BED + 3.0   # nothing held: nothing paid
    assert S.hold_cost(st) == 0.0


def test_low_capacity_makes_endings_harder_to_recognize():
    S = _sim()
    st = S.State()
    st.capacity = 1.0
    full = S.threshold(st, 0.4)
    st.capacity = 0.2
    assert S.threshold(st, 0.4) > full


def test_rest_arrives_on_its_own_at_zero_capacity_and_restores_it():
    S = _sim()
    hi = _runs(S, 0.9, seeds=10)
    assert sum(d["rest_arrived"] for r in hi for d in r["days"]) > 0
    for r in hi:                                # capacity never goes below empty, never above full
        assert all(0.0 <= d["capacity_morning"] <= 1.0 for d in r["days"])


def test_writing_it_down_brings_sleep_sooner():
    S = _sim()
    plain, written = _runs(S, 0.6), _runs(S, 0.6, offload=True)
    assert _mean(written, "onset_min") < _mean(plain, "onset_min")


def test_clear_endings_settle_more_at_their_end():
    S = _sim()
    plain, clear = _runs(S, 0.6), _runs(S, 0.6, clear=True)
    assert _mean(clear, "settled_at_end_share") > _mean(plain, "settled_at_end_share")
    assert _mean(clear, "open_at_bed") < _mean(plain, "open_at_bed")


# ---- the channel dial: a detailed tracker, a narrow way to settle ----------------


def test_a_wide_channel_is_the_toy_without_the_dial():
    S = _sim()
    a = S.simulate(0.5, days=5, seed=4)
    b = S.simulate(0.5, days=5, seed=4, channel=None, detail=1.0)
    assert a == b
    assert all(d["backlog_at_bed"] == 0 and d["peak_backlog"] == 0 for d in a["days"])


def test_a_narrow_channel_builds_a_backlog_and_more_detail_builds_more():
    S = _sim()
    thin = _runs(S, 0.3, channel=4.0, detail=1.0)
    rich = _runs(S, 0.3, channel=4.0, detail=3.0)
    assert _mean(thin, "peak_backlog") > 0
    assert _mean(rich, "peak_backlog") > _mean(thin, "peak_backlog")


def test_rest_drains_the_backlog():
    S = _sim()
    runs = _runs(S, 0.3, channel=4.0, detail=3.0)
    assert _mean(runs, "backlog_morning") < _mean(runs, "backlog_at_bed")


def test_settling_through_the_channel_costs_capacity_while_awake():
    S = _sim()
    st = S.State()
    st.queue = 10.0
    moved, cost = S.drain(st, channel=6.0, asleep=False)
    assert moved == 6.0 / 60 and cost > 0
    st.queue = 10.0
    moved_r, cost_r = S.drain(st, channel=6.0, asleep=True)
    assert moved_r > moved and cost_r == 0.0     # rest settles faster, for free
    st.queue = 0.0
    assert S.drain(st, channel=6.0, asleep=False) == (0.0, 0.0)


def test_a_rich_tracker_through_a_narrow_channel_rests_unbidden_more():
    S = _sim()
    wide = _runs(S, 0.3, detail=3.0)
    narrow = _runs(S, 0.3, channel=4.0, detail=3.0)
    assert _mean(narrow, "rest_arrived") > _mean(wide, "rest_arrived")
