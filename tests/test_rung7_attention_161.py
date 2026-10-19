"""#161: a joint-attention question for rung 7 -- the scoring code of the
replay in experiments/rung7_attention/ (which entity will the operator touch
next, scored against "whatever they touch most").

Only the scoring and the no-lookahead discipline are pinned here; the
numbers come from a private copy of the live record and live in the
experiment's README.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "experiments" / "rung7_attention"))

import attention as A  # noqa: E402


def turns(*sets, t0=1000.0, step=60.0):
    return [{"ts": t0 + i * step, "ents": list(s)} for i, s in enumerate(sets)]


def test_norm_folds_namespace_and_class_and_drops_channels():
    assert A.norm("membrane::Component:w:body") == "w:body"
    assert A.norm("w:body") == "w:body"
    assert A.norm("Component:Toy:Die") == "toy:die"
    assert A.norm("membrane::w:prime") == "w:prime"
    # the channel the operator speaks through is not what they attend to
    assert A.norm("membrane::Component:chat:in:roomata-estorsky") is None
    assert A.norm("chat:out:operator") is None
    assert A.norm("person:operator") is None
    assert A.norm("") is None
    assert A.norm(None) is None
    # the mirror/model views of an entity are the entity
    assert A.norm("m:test:re-149") == "test:re-149"
    assert A.norm("m:component:toy:die") == "toy:die"
    assert A.norm("models:component:test:re-150") == "test:re-150"
    assert A.norm("files:~self_loop.py") == "f:~self_loop.py"
    # a channel under any prefix is still the channel
    assert A.norm("config:chat:out:roomata-estorsky") is None
    assert A.norm("m:chat:out:roomata-estorsky") is None


def test_popularity_log_loss_with_the_novel_bucket_by_hand():
    # a, a, b: the first touch is novel (eps = 1), the second sees a once
    # (eps = 1/(1+1)), the third is novel again (eps = 1/(2+1))
    res = A.run(turns(["a"], ["a"], ["b"]), A.Popularity())
    lps = [r["lp"] for r in res]
    assert math.isclose(lps[0], 0.0)                      # log 1: only the bucket
    assert math.isclose(lps[1], math.log(0.5))            # (1 - 1/2) * 1
    assert math.isclose(lps[2], math.log(1 / 3))          # the bucket, eps = 1/3
    assert [r["rank"] for r in res] == [None, 1, None]    # novel = a miss


def test_novel_bucket_is_shared_so_novel_events_cost_every_predictor_the_same():
    seq = turns(["a"], ["b"], ["c"], ["a"], ["d"])
    for pred in (A.Popularity(), A.Decayed(half=2, lam=0.7), A.Bigram(beta=1.0)):
        res = A.run(seq, pred)
        assert math.isclose(res[4]["lp"], math.log(3 / 7))   # 3 distinct of 4 events + 3


def test_distribution_is_proper():
    pred = A.Bigram(beta=0.5)
    for t in turns(["a", "b"], ["c"], ["a"], ["b"]):
        q = pred.predict(t["ts"])
        if q:
            assert math.isclose(sum(q.values()), 1.0)
            assert all(v >= 0 for v in q.values())
        pred.observe(t)


def test_no_lookahead_changing_the_future_does_not_change_the_past():
    a = turns(["a"], ["b"], ["a"], ["c"], ["a"], ["b"])
    b = turns(["a"], ["b"], ["a"], ["z"], ["z"], ["z"])
    for mk in (A.Popularity, lambda: A.Decayed(half=3, lam=0.5), lambda: A.Bigram(beta=1.0)):
        ra, rb = A.run(a, mk()), A.run(b, mk())
        assert [r["lp"] for r in ra[:4]] == [r["lp"] for r in rb[:4]]


def test_events_in_one_turn_are_predicted_from_before_the_turn():
    # b and a in one turn: both are scored by the state BEFORE that turn
    res = A.run(turns(["a"], ["b", "a"]), A.Popularity())
    assert res[1]["e"] == "b" and res[1]["rank"] is None
    assert res[2]["e"] == "a" and res[2]["rank"] == 1
    assert math.isclose(res[2]["lp"], math.log(0.5))


def test_bigram_beats_popularity_on_a_cycle_and_skill_says_so():
    seq = turns(*([["a"], ["b"], ["c"]] * 30))
    base = A.run(seq, A.Popularity())
    big = A.run(seq, A.Bigram(beta=0.1))
    assert A.skill(big, base) > 0.5
    assert A.hit(big, 1) > A.hit(base, 1)
    assert math.isclose(A.skill(base, base), 0.0)


def test_bootstrap_ci_brackets_the_point_and_resamples_whole_turns():
    seq = turns(*([["a", "b"], ["c"]] * 20))
    base = A.run(seq, A.Popularity())
    big = A.run(seq, A.Bigram(beta=0.1))
    pt = A.skill(big, base)
    lo, hi = A.boot_ci(big, base, A.skill, n=300, seed=1)
    assert lo <= pt <= hi
    # identical predictors: a degenerate CI at exactly zero
    lo, hi = A.boot_ci(base, base, A.skill, n=50, seed=1)
    assert lo == hi == 0.0


def test_mix_is_proper_and_its_ends_are_its_parts():
    seq = turns(["a"], ["b"], ["a", "c"], ["b"], ["a"])
    for w, same in ((1.0, lambda: A.Decayed(2, 0.6)), (0.0, lambda: A.Bigram(1.0))):
        mixed = A.run(seq, A.Mix(A.Decayed(2, 0.6), A.Bigram(1.0), w))
        alone = A.run(seq, same())
        assert all(math.isclose(x["lp"], y["lp"]) for x, y in zip(mixed, alone))
    m = A.Mix(A.Decayed(2, 0.6), A.Bigram(1.0), 0.5)
    for t in seq:
        q = m.predict(t["ts"])
        if q:
            assert math.isclose(sum(q.values()), 1.0)
        m.observe(t)


def test_a_named_unseen_candidate_is_scored_out_of_the_bucket():
    class Oracle(A.Popularity):
        def predict(self, ts):
            return {"z": 1.0}
    res = A.run(turns(["a"], ["z"]), Oracle())
    # eps = 1/2 at the second turn; z was named with q = 1 -> p = 1/2, a hit
    assert math.isclose(res[1]["lp"], math.log(0.5)) and res[1]["rank"] == 1
