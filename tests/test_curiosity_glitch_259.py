"""#259: curiosity must not chase restart glitches.

The heartbeat thing is predicted perfectly (errors ~0) except for a rare miss
near 1.0 when the seat restarts or stalls and the heartbeat skips. With a plain
windowed mean, that miss ageing out of the earlier window read as "learning
progress", and self kept going back to the heartbeat. Two guards:

  - a look whose interval (since the same thing's previous look) spans a gap in
    the record longer than CURIO_GAP_S -- a restart or a stall, seen from the
    record's timestamps alone -- is not an ordinary error for progress;
  - each window's error is its interquartile mean (CURIO_TRIM off each end), so
    a rare outlier the record can't see either can't make progress.
"""
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402

W = SL.CURIO_WINDOW


def _run(spec, t0=1000.0, pace=None):
    """A record: one cycle line and one look per step, PACE_S apart. spec is a
    list of (eid, answer, p, gap_before) -- gap_before adds that many seconds
    of silence in the record before the step (a restart / stall)."""
    pace = SL.PACE_S if pace is None else pace
    t, n, out = t0, 0, []
    for eid, answer, p, gap in spec:
        t += pace + gap
        n += 1
        out.append({"ts": t, "kind": "cycle", "n": n, "act": "move"})
        out.append({"ts": t, "kind": "reach", "n": n, "eid": eid, "answer": answer, "p": p})
    return out


def _mastered(miss_at, gap_before_miss=0.0):
    """2W + 1 looks at a perfectly predicted heartbeat, one miss (answer
    unchanged at p 0.99) at index miss_at, optionally after a restart gap (so
    two full windows remain when the spanning miss is left out)."""
    spec = []
    for i in range(2 * W + 1):
        if i == miss_at:
            spec.append(("I", "unchanged", 0.99, gap_before_miss))
        else:
            spec.append(("I", "changed", 1.0, 0.0))
    return spec


# ---- a mastered thing with one restart-spanning miss: ~0 progress ---------------

def test_a_mastered_thing_with_one_restart_spanning_miss_has_no_progress():
    recs = _run(_mastered(miss_at=5, gap_before_miss=3600.0))    # the miss sits in the EARLIER window
    lp = SL._progress(recs)
    assert abs(lp["I"]) < SL.CURIO_MIN
    assert SL._curiosity(recs, ["I"]) == {"I": 1.0}             # no frontier anywhere: untilted


def test_a_single_miss_the_record_cannot_see_is_no_progress_either():
    """A stall shorter than CURIO_GAP_S leaves no trace in the record; the
    robust window statistic still keeps one outlier from becoming progress."""
    recs = _run(_mastered(miss_at=5))
    assert abs(SL._progress(recs)["I"]) < SL.CURIO_MIN


def test_the_heartbeat_does_not_steal_the_tilt_from_a_learnable_thing():
    spec = _mastered(miss_at=5, gap_before_miss=3600.0)
    # interleave a genuinely improving thing: p of its (always changed) answer rises
    learn = [("L", "changed", 0.4 + 0.55 * i / (2 * W), 0.0) for i in range(2 * W + 1)]
    mixed = [x for pair in zip(spec, learn) for x in pair]
    tilt = SL._curiosity(_run(mixed), ["I", "L"])
    assert tilt["L"] == pytest.approx(1.0)
    assert tilt["I"] == pytest.approx(SL.CURIO_FLOOR)


# ---- a genuinely improving thing keeps positive progress -------------------------

def test_a_genuinely_improving_thing_keeps_positive_progress():
    spec = [("L", "changed", 0.4 + 0.55 * i / (2 * W - 1), 0.0) for i in range(2 * W)]
    lp = SL._progress(_run(spec))
    assert lp["L"] > 0.05


def test_an_improving_thing_with_frequent_misses_that_get_rarer_keeps_progress():
    """Misses are a learnable thing's bulk, not an outlier: half wrong early,
    rarely wrong late, is still progress under the robust statistic."""
    early = [("L", "unchanged" if i % 2 else "changed", 0.8, 0.0) for i in range(W)]
    late = [("L", "unchanged" if i % 10 == 9 else "changed", 0.8, 0.0) for i in range(W)]
    assert SL._progress(_run(early + late))["L"] > SL.CURIO_MIN


# ---- a thing that genuinely got worse stays at the floor -------------------------

def test_a_thing_that_got_worse_is_unaffected_and_stays_at_the_floor():
    spec = [("B", "changed", 0.95 - 0.55 * i / (2 * W - 1), 0.0) for i in range(2 * W)]
    learn = [("L", "changed", 0.4 + 0.55 * i / (2 * W - 1), 0.0) for i in range(2 * W)]
    recs = _run([x for pair in zip(spec, learn) for x in pair])
    assert SL._progress(recs)["B"] < 0
    assert SL._curiosity(recs, ["B", "L"])["B"] == pytest.approx(SL.CURIO_FLOOR)


# ---- restart detection reads the record's timestamps only -----------------------

def test_a_look_spanning_a_record_gap_is_not_an_ordinary_error():
    spec = [("I", "changed", 1.0, 0.0)] * 5 + [("I", "unchanged", 0.99, SL.CURIO_GAP_S + 1.0)]
    errs = SL._ordinary_errors(_run(spec))["I"]
    assert len(errs) == 5 and max(errs) == 0.0


def test_the_gap_may_fall_anywhere_since_the_things_previous_look():
    """The restart lands while self looks at OTHER things; the heartbeat's next
    look still spans it."""
    spec = ([("I", "changed", 1.0, 0.0)] * 3 + [("X", "changed", 0.5, 0.0)]
            + [("X", "changed", 0.5, SL.CURIO_GAP_S + 1.0)] + [("X", "changed", 0.5, 0.0)]
            + [("I", "unchanged", 0.99, 0.0)] + [("I", "changed", 1.0, 0.0)])
    errs = SL._ordinary_errors(_run(spec))
    assert errs["I"] == [0.0] * 4                     # the spanning miss left out; the look after counts
    assert len(errs["X"]) == 2                        # X's own look right after the gap spans it too


def test_a_gap_under_the_threshold_is_ordinary():
    spec = [("I", "changed", 1.0, 0.0)] * 3 + [("I", "unchanged", 0.99, SL.CURIO_GAP_S - SL.PACE_S - 1.0)]
    assert len(SL._ordinary_errors(_run(spec))["I"]) == 4


def test_restart_detection_reads_timestamps_not_other_fields():
    """A look's own claims (a huge `dt`, a `why`) say nothing about a restart;
    only the gap between the record's lines does."""
    recs = _run([("I", "changed", 1.0, 0.0)] * 3 + [("I", "unchanged", 0.99, 0.0)])
    recs[-1].update({"dt": 1e6, "why": "restart"})
    assert len(SL._ordinary_errors(recs)["I"]) == 4
    assert SL.CURIO_GAP_S > SL.WAIT_EVERY                 # a recorded wait stretch is not a stall (#343: WAIT_EVERY is s)
