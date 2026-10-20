"""#161 RUNG 8 (rung 7 until the #258 ladder flip): understand an other.

#258 moved this test up one rung, unchanged: the seats here have passed the
new rung 7 (take turns) and may grow to 8; the model is asked only once
rung 8 is available; RUNG7_* are RUNG8_*. These tests keep the timer's pace
(SL.TURNS off) -- rung 7's learned timing has its own tests
(test_self_rung7_turns_258) and only changes WHEN a push goes, not what a
reply check reads.

Self predicts the other's answer at the reply check from its own record
(p_self, #254) and may consult the model coprocessor as an advisor
(`llm::call`, asked at each push, read by `expect` at the reply check --
#215), mixing its answer in by the advisor's own track record. It passes
when its prediction removes RUNG8_MARGIN of the running rate's Brier error
over the last RUNG8_LOOKS reply checks.
"""
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
RESP = MEMBRANE_NS + "Component:chat:in:operator"    # the other self understands


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


def _dir(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


class _Chatter:
    """The other, on a period-4 (changed, changed, unchanged, unchanged, ...):
    a pattern habit's one bit of memory can't catch (it settles on the 0.5 base
    rate) but a full-history predictor can. p=0.5, so self classes it
    intermittent -- no rung-5 audit pauses its getter mid-test."""
    def __init__(self):
        self.looks = {}

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > time.time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        self.looks[name] = self.looks.get(name, 0) + 1
        if "chat:in" in ent.eid:
            changed = (self.looks[name] % 4) in (1, 2)     # C, C, U, U, C, C, ...
            return {"fired": True, "state": {"heard": self.looks[name]}, "changed": changed}
        return {"fired": True, "state": {"v": self.looks[name]}, "changed": False}


def _llm_pred(chatter):
    """The model stand-in: predicts the phase of the period-4 pattern, near-
    perfectly, for the look that is about to happen."""
    def pred(srv, eid, since=None, until=None):
        name = eid.split(":")[-1]
        next_look = chatter.looks.get(name, 0) + 1
        return 0.9 if (next_look % 4) in (1, 2) else 0.1
    return pred


def _queued(srv, eid, prompt):
    """The consult went out (its answer is the _llm_pred stand-in's)."""
    return {"rc": 0, "text": f"queued: a loop takes it up on {eid}"}


# the pairs come one per push (#215): enough pushes for RUNG8_LOOKS reply checks
CYCLES = (SL.RUNG8_LOOKS + 5) * SL.MEET_EVERY


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    WA._getter_paused.clear()
    monkeypatch.setattr(SL, "TURNS", False)          # #258: the timer's pace (see the module doc)


def _seat(names=(), limit=6, passed=(1, 2, 3, 4, 5)):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _World(_dir(1))
    est._srv = srv
    for name in names:
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:" + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    CIO.entity_in(srv, CIO.OPERATOR)                   # the other self understands (a real reach target)
    SL.bind(srv)
    SL.set_limit(srv, 1)
    for r in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9, "ladder": SL.LADDER_V})
    SL.set_limit(srv, limit)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _kind(srv, k):
    return [r for r in _record(srv) if r["kind"] == k]


def test_a_reach_at_the_other_carries_the_models_prediction(monkeypatch):
    chatter = _Chatter()
    monkeypatch.setattr(SL, "_read_entity", chatter)
    monkeypatch.setattr(SL, "_llm_pred", _llm_pred(chatter))
    monkeypatch.setattr(SL, "_ask_llm", _queued)
    srv = _seat(limit=8, passed=(1, 2, 3, 4, 5, 6, 7))
    _run(srv, 60)
    reaches = [r for r in _kind(srv, "reach") if r["eid"] == RESP]
    assert reaches
    with_llm = [r for r in reaches if "p_llm" in r]
    assert with_llm
    assert all("p" in r for r in reaches)                      # habit still there, same look
    assert all(0.0 <= r["p_llm"] <= 1.0 for r in with_llm)


def test_rung_eight_opens_with_an_advisor_that_knows_the_phase(monkeypatch):
    # #254: the advisor earns its weight on the record and self's mix passes
    chatter = _Chatter()
    monkeypatch.setattr(SL, "_read_entity", chatter)
    monkeypatch.setattr(SL, "_llm_pred", _llm_pred(chatter))
    monkeypatch.setattr(SL, "_ask_llm", _queued)
    srv = _seat(limit=8, passed=(1, 2, 3, 4, 5, 6, 7))
    _run(srv, CYCLES)
    assert 8 in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]
    assert SL.status(srv)["rung"] == 8
    assert SL.status(srv)["rung8"]["advisor_weight"] > 0.5


def _checks(outcomes, p_llm=None):
    """A record of reply checks at the other: one push and its check each."""
    recs, t = [], 1000.0
    for i, o in enumerate(outcomes):
        recs.append({"ts": t, "kind": "push", "to": RESP, "asked": p_llm is not None})
        r = {"ts": t + 120.0, "kind": "reach", "eid": RESP, "reply": True, "p": 0.5,
             "answer": "changed" if o else "unchanged", "replied": bool(o)}
        if p_llm is not None:
            r["p_llm"], r["llm_model"] = p_llm[i], "m"
        recs.append(r)
        t += 300.0
    return recs


def test_a_phase_aware_advisor_lifts_self_over_the_running_rate():
    # the other runs period-4 (C, C, U, U): self's own inputs (rates, the last
    # bit) can't see the phase; an advisor that does earns its weight
    outcomes = [1 if i % 4 in (0, 1) else 0 for i in range(SL.RUNG8_LOOKS + 20)]
    out = SL._rung8(_checks(outcomes, [0.9 if o else 0.1 for o in outcomes]))
    assert out["passed"] is True and out["skill"] > 0.3


def test_an_advisor_that_echoes_the_rate_adds_nothing():
    # an advisor that says the running rate removes none of its error
    import random
    rng = random.Random(5)
    outcomes = [1 if rng.random() < 0.6 else 0 for _ in range(SL.RUNG8_LOOKS + 20)]
    k, echo = 0, []
    for i, o in enumerate(outcomes):
        echo.append((k + 0.5) / (i + 1.0))
        k += o
    out = SL._rung8(_checks(outcomes, echo))
    assert out["skill"] < SL.RUNG8_MARGIN and out["passed"] is False


def test_the_limit_holds_rung_eight(monkeypatch):
    # one rung up from the old "limit 6 holds rung 7": rung 7 passed, limit 7
    chatter = _Chatter()
    monkeypatch.setattr(SL, "_read_entity", chatter)
    monkeypatch.setattr(SL, "_llm_pred", _llm_pred(chatter))
    monkeypatch.setattr(SL, "_ask_llm", _queued)
    srv = _seat(limit=7, passed=(1, 2, 3, 4, 5, 6, 7))
    _run(srv, 400)
    assert not [r for r in _kind(srv, "reach") if r["eid"] == RESP and "p_llm" in r]
    assert 8 not in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]


@pytest.fixture(autouse=True)
def _chat_medium(monkeypatch):
    """These are the chat medium's tests (a line to chat:out is the turn): #264 moved
    rung 7's turns to the ball by default, so they switch the toy medium off."""
    monkeypatch.setattr(SL, "TOY_TURNS", False)
