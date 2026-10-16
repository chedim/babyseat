"""#161 LAYER 1 IN THE LAB: can a contingency ledger find a body it was never
told about? A synthetic world with planted ground truth -- entities `self`'s
acts move reliably (body), agents that answer it late and unreliably (others),
and background churn (world). The ledger must recover the truth from the event
stream alone, and a shuffled-act null must find nothing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "experiments" / "self_l1"))

import contingency as C  # noqa: E402
import sandbox as SB  # noqa: E402


def test_a_planted_body_is_found_and_noise_is_not():
    w = SB.World(seed=7)
    acts, changes = w.run(steps=6000)
    led = C.Ledger(window=2.0)
    led.observe(acts, changes, horizon=6000)
    cls = C.classify(led.stats())
    assert set(cls["body"]) == set(w.truth["body"])
    assert not set(cls["body"]) & set(w.truth["world"])


def test_others_are_told_apart_from_body():
    w = SB.World(seed=11)
    acts, changes = w.run(steps=8000)
    led = C.Ledger(window=12.0)
    led.observe(acts, changes, horizon=8000)
    cls = C.classify(led.stats())
    assert set(cls["other"]) >= set(w.truth["other"])
    assert not set(cls["other"]) & set(w.truth["body"])


def test_shuffled_acts_find_no_body():
    w = SB.World(seed=3)
    acts, changes = w.run(steps=6000)
    led = C.Ledger(window=2.0)
    led.observe(C.shuffled(acts, horizon=6000, seed=1), changes, horizon=6000)
    assert C.classify(led.stats())["body"] == []


def test_the_body_is_stable_across_halves():
    w = SB.World(seed=5)
    acts, changes = w.run(steps=8000)
    a, b = C.Ledger(window=2.0), C.Ledger(window=2.0)
    a.observe([x for x in acts if x[0] < 4000], [x for x in changes if x[0] < 4000], horizon=4000)
    b.observe([(t - 4000, k) for t, k in acts if t >= 4000],
              [(t - 4000, e) for t, e in changes if t >= 4000], horizon=4000)
    ba, bb = set(C.classify(a.stats())["body"]), set(C.classify(b.stats())["body"])
    assert ba and len(ba & bb) / len(ba | bb) >= 0.8


def test_one_stray_late_answer_does_not_unmake_a_body_part():
    # a body part that answers 69 acts at once and one act 5 steps late (its
    # answer to the NEXT act, counted against this one) is still body --
    # steadiness must be robust to strays (the std of the delays was not)
    acts = [(t * 20.0, "write") for t in range(70)]
    changes = [(t * 20.0 + (t % 10) / 10.0, "b:part") for t in range(69)] + [(69 * 20.0 + 10.0, "b:part")]
    led = C.Ledger(window=12.0)
    led.observe(acts, changes, horizon=70 * 20.0 + 20)
    assert C.classify(led.stats())["body"] == ["b:part"]
