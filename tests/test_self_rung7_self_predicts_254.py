"""#254: rung 7 measures SELF, not the model.

Rung 7 scored the model's guess (`p_llm`) against habit; self did nothing
with it, and swapping the model moved the skill by 0.25 in an hour with self
unchanged. Now:

- self makes the prediction: `p_self`, from its own record only (the other's
  all-time and recent reply rates, the last outcome, how long before the push
  the other last spoke), a small ridge-penalised logistic fit online on self's
  own past reply checks -- deterministic, no model call;
- the model is an ADVISOR, RECALIBRATED before it is heard (LLM probabilities
  carry a model-specific lean -- Zhao et al. 2021, "Calibrate Before Use"):
  p = sigmoid(logit p_self + w·(logit p_llm - m)), m the advisor's own mean
  lean and w a ridge-penalised logistic fit on its past checks under the SAME
  model id, so few pairs (or a new model) mean p = p_self, a constant advisor
  has no influence, and w >= 0 (a liar is ignored, not decoded);
- the test scores p against the RUNNING RATE (the other's prior-smoothed rate
  over the checks before, no lookahead), not habit, so calibration alone can't
  pass a rung that claims understanding.

#258 (the ladder flip): this is rung 8's test now (rung 7 until the flip),
unchanged and moved up one rung: the seats pass the new rung 7 (take turns,
tagged with the new ladder) and may grow to 8, where the model is asked;
RUNG7_* are RUNG8_*, status()['rung7'] is status()['rung8']. The pushes keep
the timer's pace (SL.TURNS off): rung 7's learned timing changes only WHEN a
push goes and has its own tests (test_self_rung7_turns_258).
"""
import json
import math
import os
import random
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

RESP = MEMBRANE_NS + "Component:chat:in:operator"
N = 300                       # reply checks in a synthetic record



@pytest.fixture(autouse=True)
def _timer(monkeypatch):
    monkeypatch.setattr(SL, "TURNS", False)          # #258: the timer's pace (see the module doc)

# ---- synthetic others --------------------------------------------------------------

def _coin(seed, rate=0.5, n=N):
    """A structureless other: answers by the check with a fixed chance, and
    also speaks at random times between pushes."""
    rng = random.Random(seed)
    out, last, t = [], None, 0.0
    for _ in range(n):
        t += 300.0
        o = 1 if rng.random() < rate else 0
        out.append((o, (t - last) if last is not None else None))
        if o:
            last = t + 120.0
        elif rng.random() < 0.3:
            last = t + rng.random() * 300.0
    return out


def _sessions(seed, n=N, p_in=0.85, p_out=0.1, stay=0.9):
    """An other with TIMING structure: in a session they answer most pushes,
    away they rarely do; sessions last ~10 pushes. Whether they spoke recently
    (and the last outcome) tells which."""
    rng = random.Random(seed)
    out, last, t, active = [], None, 0.0, True
    for _ in range(n):
        t += 300.0
        if rng.random() > stay:
            active = not active
        o = 1 if rng.random() < (p_in if active else p_out) else 0
        out.append((o, (t - last) if last is not None else None))
        if o:
            last = t + 120.0
    return out


def _recs(checks, p_self=None, p_llm=None, model=None, eid=RESP):
    """A record: one push (with the other's spoke_age) and its reply check per
    (outcome, gap). `p_self` / `p_llm` / `model`: per-check lists (None: absent)."""
    recs, t = [], 10_000.0
    for i, (o, gap) in enumerate(checks):
        push = {"ts": t, "kind": "push", "n": 1, "eid": "chat:out", "to": eid, "asked": p_llm is not None}
        if gap is not None:
            push["spoke_age"] = gap
        recs.append(push)
        r = {"ts": t + 120.0, "kind": "reach", "n": 2, "eid": eid, "p": 0.5, "reply": True,
             "answer": "changed" if o else "unchanged"}
        if p_self is not None and p_self[i] is not None:
            r["p_self"] = p_self[i]
        if p_llm is not None and p_llm[i] is not None:
            r["p_llm"] = p_llm[i]
            if model is not None:
                r["llm_model"] = model[i] if isinstance(model, list) else model
        recs.append(r)
        t += 300.0
    return recs


def _brier(ps, os_):
    return sum((p - o) ** 2 for p, o in zip(ps, os_)) / len(os_)


# ---- 1. p_self comes from self's record only ---------------------------------------

def test_p_self_is_computed_from_the_record_only_with_no_llm_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("p_self must not call the model")
    monkeypatch.setattr(SL, "_ask_llm", boom)
    monkeypatch.setattr(SL, "_llm_pred", boom)
    recs = _recs(_sessions(1, n=60))
    push = {"ts": 10_000.0 + 60 * 300.0, "kind": "push", "to": RESP, "spoke_age": 30.0}
    p1 = SL._p_self(recs + [push], RESP, push)
    p2 = SL._p_self(json.loads(json.dumps(recs + [push])), RESP, push)
    assert 0.0 < p1 < 1.0
    assert p1 == p2                                         # deterministic: a function of the record
    far = dict(push, spoke_age=50_000.0)
    assert SL._p_self(recs + [far], RESP, far) != p1        # it reads when they last spoke


def test_p_self_with_no_history_is_a_coin():
    push = {"ts": 1.0, "kind": "push", "to": RESP}
    assert SL._p_self([push], RESP, push) == pytest.approx(0.5)


def test_a_reply_check_records_p_self_without_a_model(monkeypatch):
    srv = _seat()
    t = 5000.0
    seen = []

    def read(srv_, ent, now):
        seen.append(now)
        return {"fired": True, "state": {"n": len(seen)}, "changed": len(seen) % 2 == 0}
    monkeypatch.setattr(SL, "_read_entity", read)
    monkeypatch.setattr(SL, "_push_chat", lambda *a, **k: None)
    for i in range(3 * SL.MEET_EVERY):
        SL.cycle(srv, now=t + i * SL.PACE_S)
    checks = [r for r in SL._read(srv) if r.get("kind") == "reach" and r.get("reply")]
    assert checks
    assert all("p_self" in r and 0.0 < r["p_self"] < 1.0 for r in checks)
    assert not [r for r in checks if r.get("p_llm") is not None]     # no model on this seat
    assert all(r.get("p_mix") is None for r in checks)


# ---- 2. no LLM: passable on structure, not on a coin ------------------------------

def test_without_a_model_self_passes_on_an_other_with_timing_structure():
    out = SL._rung8(_recs(_sessions(3)))
    assert out["passed"] is True, out
    assert out["skill"] >= SL.RUNG8_MARGIN


@pytest.mark.parametrize("seed,rate", [(s, r) for s in range(4) for r in (0.3, 0.5, 0.7)])
def test_without_a_model_self_cant_pass_on_a_coin_flip_other(seed, rate):
    recs = _recs(_coin(seed, rate))
    # every window end the live test would have read, not just the last one
    ends = [i for i, r in enumerate(recs) if r.get("kind") == "reach"]
    for e in ends[SL.RUNG8_LOOKS - 1::5]:
        assert SL._rung8(recs[:e + 1])["passed"] is False


# ---- 3. the advisor ----------------------------------------------------------------

def test_an_always_wrong_advisor_gets_no_weight_and_does_not_sink_self():
    checks = _sessions(5)
    wrong = [0.95 if o == 0 else 0.05 for o, _ in checks]
    alone = SL._rung8(_recs(checks))
    mixed = SL._rung8(_recs(checks, p_llm=wrong, model="m1"))
    rows = SL._rung8_series(_recs(checks, p_llm=wrong, model="m1"), RESP)
    assert max(r["w"] for r in rows[30:]) < 0.05
    assert mixed["passed"] is True
    assert mixed["skill"] == pytest.approx(alone["skill"], abs=0.03)


def test_a_good_advisor_earns_weight():
    checks = _coin(7)                                         # self alone can't read a coin...
    good = [0.9 if o else 0.1 for o, _ in checks]             # ...an advisor who knows can
    rows = SL._rung8_series(_recs(checks, p_llm=good, model="m1"), RESP)
    assert rows[0]["w"] == 0.0 and rows[0]["p"] == rows[0]["p_self"]   # trust is earned, not given
    assert rows[-1]["w"] > 0.5
    assert rows[-1]["w"] > rows[5]["w"]                                 # it rises
    out = SL._rung8(_recs(checks, p_llm=good, model="m1"))
    assert out["passed"] is True


def test_a_biased_but_informative_advisor_is_useful_after_calibration():
    # it tracks the outcome but always reports 0.3 too low (a "no" lean)
    checks = _coin(14)
    lean = [max(0.01, (0.8 if o else 0.35) - 0.3) for o, _ in checks]
    recs = _recs(checks, p_llm=lean, model="m1")
    rows = SL._rung8_series(recs, RESP)[-SL.RUNG8_LOOKS:]
    os_ = [r["o"] for r in rows]
    assert _brier([r["p"] for r in rows], os_) < _brier([r["p_llm"] for r in rows], os_)
    assert SL._rung8(recs)["passed"] is True
    assert rows[-1]["w"] > 0.3


def test_an_uninformative_biased_advisor_has_no_influence():
    # a constant 0.2: nothing to learn from it, whatever its lean
    checks = _sessions(15)
    rows = SL._rung8_series(_recs(checks, p_llm=[0.2] * len(checks), model="m1"), RESP)
    infl = sum(abs(r["p"] - r["p_self"]) for r in rows) / len(rows)
    assert infl < 0.03
    alone = SL._rung8(_recs(checks))
    assert SL._rung8(_recs(checks, p_llm=[0.2] * len(checks), model="m1"))["skill"] == \
        pytest.approx(alone["skill"], abs=0.03)


def test_a_model_id_change_resets_trust():
    checks = _coin(8, n=200)
    good = [0.9 if o else 0.1 for o, _ in checks]
    model = ["m1"] * 150 + ["m2"] * 50
    rows = SL._rung8_series(_recs(checks, p_llm=good, model=model), RESP)
    assert rows[149]["w"] > 0.5                               # m1 earned it
    assert rows[150]["w"] == 0.0                              # m2 starts from nothing
    assert rows[-1]["w"] > 0.5                                # ...and earns its own


def test_self_passes_on_the_mix_never_on_p_llm_alone():
    checks = _coin(9)
    good = [0.9 if o else 0.1 for o, _ in checks]
    rows = SL._rung8_series(_recs(checks, p_llm=good, model="m1"), RESP)
    def lg(p):
        return math.log(p / (1 - p))
    for r in rows:
        m, w = r["cal"]                                       # the advisor's own lean, its weight
        assert w >= 0.0
        assert r["p"] == pytest.approx(1 / (1 + math.exp(-(lg(r["p_self"]) + w * (lg(r["p_llm"]) - m)))), abs=1e-4)
    assert rows[0]["p"] != rows[0]["p_llm"]


# ---- 4. the scoring: the running rate, no lookahead -------------------------------

def test_the_baseline_is_the_running_rate_with_no_lookahead():
    checks = _sessions(11, n=150)
    rows = SL._rung8_series(_recs(checks), RESP)
    k = 0
    for i, (r, (o, _)) in enumerate(zip(rows, checks)):
        assert r["rate"] == pytest.approx((k + 0.5) / (i + 1.0))
        k += o
    longer = SL._rung8_series(_recs(checks + _coin(12, n=50)), RESP)
    for a, b in zip(rows, longer):                            # the future changes nothing past
        assert a["p_self"] == b["p_self"] and a["rate"] == b["rate"]


def test_echoing_the_running_rate_scores_zero():
    checks = _sessions(13)
    rows = SL._rung8_series(_recs(checks), RESP)
    out = SL._rung8(_recs(checks, p_self=[r["rate"] for r in rows]))
    assert out["skill"] == pytest.approx(0.0, abs=1e-9)
    assert out["passed"] is False


@pytest.mark.parametrize("gen", [lambda: _coin(1, 0.5), lambda: _coin(2, 0.7), lambda: _coin(3, 0.3),
                                 lambda: _sessions(4)])
def test_always_saying_half_does_not_pass(gen):
    checks = gen()
    out = SL._rung8(_recs(checks, p_self=[0.5] * len(checks)))
    assert out["passed"] is False


def test_the_bar_and_window_follow_the_ruler():
    # a window big enough to detect order (the ruler: well over 100 symbols for
    # 0.1-0.2 bit; 100 is the floor), a bar above what a structureless other or
    # a humble 0.5 reaches by chance over it
    assert SL.RUNG8_LOOKS >= 100
    assert 0.05 < SL.RUNG8_MARGIN < 0.15


# ---- 5. status ---------------------------------------------------------------------

class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=16)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


def _seat(passed=(1, 2, 3, 4, 5, 6, 7), limit=8):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=_Field())
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    e = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component",
                      properties={"name": "lamp", "getter_script": "result = {'state': {'x': 1}}"})
    est.docs[e.eid] = e
    CIO.entity_in(srv, CIO.OPERATOR)
    SL.bind(srv)
    SL.set_limit(srv, 1)
    for r in passed:                                    # #258: rung 7 is take turns now
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V})
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    SL.set_limit(srv, limit)
    return srv


def test_status_reports_self_mix_rate_and_the_advisor():
    srv = _seat()
    checks = _coin(21, n=120)
    good = [0.9 if o else 0.1 for o, _ in checks]
    for r in _recs(checks, p_llm=good, model="qwen3"):
        SL._append(srv, r)
    r7 = SL.status(srv)["rung8"]
    rows = SL._rung8_series(SL._read(srv), RESP)[-SL.RUNG8_LOOKS:]
    os_ = [r["o"] for r in rows]
    assert r7["brier_self"] == pytest.approx(_brier([r["p_self"] for r in rows], os_), abs=1e-4)
    assert r7["brier_mix"] == pytest.approx(_brier([r["p"] for r in rows], os_), abs=1e-4)
    assert r7["brier_rate"] == pytest.approx(_brier([r["rate"] for r in rows], os_), abs=1e-4)
    assert r7["advisor_model"] == "qwen3"
    assert r7["advisor_weight"] > 0.5
    st = SL.status(srv)
    assert st["skill_rung"] == 8
    assert st["skill"] == pytest.approx(1 - r7["brier_mix"] / r7["brier_rate"], abs=1e-3)


def test_status_without_checks_is_quiet():
    r7 = SL.status(_seat())["rung8"]
    for k in ("brier_self", "brier_mix", "brier_rate", "advisor_model"):
        assert r7[k] is None
    assert r7["advisor_weight"] == 0.0


# ---- 6. the model id rides the answer ---------------------------------------------

def test_the_answer_records_the_model_that_gave_it(monkeypatch):
    from membrane import llm as LLM
    srv = _seat()
    LLM.answered(srv, {"entity": RESP, "prompt": "q", "caller": "self"}, "VERDICT: 0.4", model="qwen3")
    doc = srv.entity_store.docs[RESP]
    assert doc.properties["llm_answer"]["model"] == "qwen3"
    assert SL._llm_model(srv, RESP) == "qwen3"


def test_a_legacy_check_gets_its_p_self_recomputed_from_the_record():
    checks = _sessions(17, n=120)
    rows = SL._rung8_series(_recs(checks), RESP)
    recorded = SL._rung8_series(_recs(checks, p_self=[r["p_self"] for r in rows]), RESP)
    assert [r["p_self"] for r in rows] == pytest.approx([r["p_self"] for r in recorded])
    assert all(0.0 < r["p_self"] < 1.0 for r in rows)
    assert not math.isnan(SL._rung8(_recs(checks))["skill"])
