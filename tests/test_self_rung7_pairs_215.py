"""#215: rung 7 never collected paired looks.

Self consulted the model only on a cycle with `n % RUNG7_EVERY == 1` whose
reach happened to target the other; the answer had to still be fresh
(RUNG7_STALE_S) at the next look at the other, which rung 4's patience made
rare; and `rt["other"]` was empty after a restart. So habit's p and the
model's p almost never met on one look, and rung 7's bar sat at 0.

Now self asks the model AT THE PUSH (rung 6's act): "will the other have
spoken by my next look at them?". After it speaks it checks for the reply --
the other is held back from ordinary choice until a reply check
RUNG7_CHECK_S after the push, and that look pairs habit's p with the
model's p. The other and the pending question come from the record, so a
restart keeps them. Caps: one consult per push, llm.request's own budget,
and a consult (or its answer landing) never runs a cycle.
"""
import json
import os
import random
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import llm as LLM  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
RESP = MEMBRANE_NS + "Component:chat:in:operator"    # the other (no people: the operator)
FAST, SLOW = 60.0, 200.0                             # the other's reply delays, alternating per push


class _Field:
    def current(self):
        v = np.random.default_rng(1).normal(size=DIM)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


class _Person:
    """The other: answers every push of self's, after FAST on even pushes and
    SLOW on odd ones -- a delay pattern a model told the facts can learn, and
    habit (one rate in time) can't. A look at chat:in reads `changed` when a
    reply landed since the last look. The lamp changes on every third look."""

    def __init__(self):
        self.t = 0.0
        self.pushes = 0
        self.replies = []
        self.last = {}
        self.lamp = 0

    def push(self, srv, text, person=""):
        delay = FAST if self.pushes % 2 == 0 else SLOW
        self.replies.append(self.t + delay)
        self.pushes += 1

    def read(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > time.time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        if "chat:in" in ent.eid:
            last = self.last.get(ent.eid, -1e18)
            changed = any(last < r <= now for r in self.replies)
            self.last[ent.eid] = now
            return {"fired": True, "state": {"heard": len(self.replies)}, "changed": changed}
        self.lamp += 1
        return {"fired": True, "state": {"v": self.lamp}, "changed": self.lamp % 3 == 0}

    def next_is_fast(self):
        """The pattern, for the push just made: replies come FAST after it?"""
        return (self.pushes - 1) % 2 == 0


class _Model:
    """The coprocessor stand-in: the answer lands on the entity as llm_answer
    (as llm.answered does). `knows`: it predicts the delay pattern; else it
    answers at random."""

    def __init__(self, person, knows=True, seed=7):
        self.person, self.knows = person, knows
        self.rng = random.Random(seed)
        self.asks = []

    def __call__(self, srv, eid, prompt):
        self.asks.append({"eid": eid, "prompt": prompt, "t": self.person.t,
                          "pushes": self.person.pushes})
        if self.knows:
            p = 0.9 if self.person.next_is_fast() else 0.1
        else:
            p = self.rng.random()
        doc = srv.entity_store.docs.get(eid)
        pp = doc.properties or {}
        pp["llm_answer"] = {"ts": self.person.t + 1.0, "text": f"{p:.3f}", "caller": "self"}
        doc.properties = pp
        return {"rc": 0, "text": f"queued: a loop takes it up on {eid}"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    WA._getter_paused.clear()
    monkeypatch.setattr(LLM, "_calls", [])
    monkeypatch.setattr(LLM, "_echo", {})


def _seat():
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
    for r in (1, 2, 3, 4, 5, 6):
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    # rung 5's audit is done: no getter pauses mid-test
    SL._append(srv, {"ts": 1.0, "kind": "audit", "rung": 5, "phase": "end", "eid": "x", "passed": True})
    SL.set_limit(srv, 7)
    return srv


def _world(monkeypatch, knows=True, seed=7):
    person = _Person()
    model = _Model(person, knows=knows, seed=seed)
    monkeypatch.setattr(SL, "_read_entity", person.read)
    monkeypatch.setattr(SL, "_push_chat", person.push)
    monkeypatch.setattr(SL, "_ask_llm", model)
    return person, model


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, person, n, t0=1000.0):
    for i in range(n):
        person.t = t0 + i * SL.PACE_S
        SL.cycle(srv, now=person.t)
    return t0 + n * SL.PACE_S


def _pushes(srv):
    return [r for r in _record(srv) if r["kind"] == "push"]


def _looks(srv):
    return [r for r in _record(srv) if r["kind"] == "reach" and r["eid"] == RESP]


def _pairs(srv):
    return [r for r in _looks(srv) if r.get("p_llm") is not None
            and r.get("answer") in ("changed", "unchanged")]


def test_after_a_push_the_model_is_consulted_once(monkeypatch):
    person, model = _world(monkeypatch)
    srv = _seat()
    _run(srv, person, 5)                                # the first cycle pushes
    pushes = _pushes(srv)
    assert len(pushes) == 1 and pushes[0]["to"] == RESP
    assert len(model.asks) == 1                         # asked AT the push, once
    assert model.asks[0]["eid"] == RESP and model.asks[0]["pushes"] == 1
    assert pushes[0].get("asked") is True
    prompt = model.asks[0]["prompt"]
    assert "probability" in prompt and "next look" in prompt
    assert "self is here" not in prompt                 # no chat text goes to the model


def test_the_next_look_at_the_other_is_a_paired_look(monkeypatch):
    person, model = _world(monkeypatch)
    srv = _seat()
    _run(srv, person, 25)
    push = _pushes(srv)[0]
    after = [r for r in _looks(srv) if r["ts"] > push["ts"]]
    assert after, "self checks for the reply after it speaks"
    first = after[0]
    assert first["ts"] - push["ts"] >= SL.RUNG7_CHECK_S  # the reply check, not sooner
    assert first["ts"] - push["ts"] <= SL.RUNG7_STALE_S
    assert first.get("reply") is True
    assert "p" in first and first.get("p_llm") is not None
    assert first["answer"] == "changed"                  # the FAST reply had landed
    assert abs(first["p_llm"] - 0.9) < 1e-6
    # one question, one pair: later looks before the next push carry no model p
    nxt = [r for r in _pushes(srv) if r["ts"] > push["ts"]]
    end = nxt[0]["ts"] if nxt else 1e18
    assert not [r for r in after[1:] if r["ts"] < end and r.get("p_llm") is not None]


def test_a_restart_still_knows_the_other_from_the_record(monkeypatch):
    person, model = _world(monkeypatch)
    srv = _seat()
    t = _run(srv, person, 2)                             # pushed and asked at cycle 1
    assert len(model.asks) == 1
    SL._RT.pop(id(srv), None)                            # restart: runtime gone
    SL.bind(srv)
    assert SL._rt(srv).get("other") in (None, RESP)
    _run(srv, person, 20, t0=t)
    assert len(model.asks) == 1                          # no new push yet: no new ask
    after = [r for r in _looks(srv) if r["ts"] > _pushes(srv)[0]["ts"]]
    assert after and after[0].get("p_llm") is not None   # the pending question survived


def test_a_learnable_reply_world_yields_enough_pairs(monkeypatch):
    person, model = _world(monkeypatch)
    srv = _seat()
    _run(srv, person, (SL.RUNG7_LOOKS + 5) * SL.MEET_EVERY)
    assert len(_pairs(srv)) >= SL.RUNG7_LOOKS


def test_a_model_that_knows_the_pattern_passes_rung_seven(monkeypatch):
    person, model = _world(monkeypatch)
    srv = _seat()
    _run(srv, person, (SL.RUNG7_LOOKS + 5) * SL.MEET_EVERY)
    assert 7 in [r["rung"] for r in _record(srv) if r["kind"] == "rung"]


def test_a_random_model_earns_no_trust(monkeypatch):
    # #254: rung 7 grades self, so a random model can neither pass it nor sink
    # it: it was measured, earned (almost) no weight, and self's mix is self's
    # own guess (this other alternates fast/slow replies, which self's last-
    # outcome input learns on its own -- see test_self_rung7_self_predicts_254)
    person, model = _world(monkeypatch, knows=False, seed=3)
    srv = _seat()
    _run(srv, person, (SL.RUNG7_LOOKS + 5) * SL.MEET_EVERY)
    assert len(_pairs(srv)) >= SL.RUNG7_LOOKS            # it was measured...
    r7 = SL.status(srv)["rung7"]
    assert r7["advisor_weight"] < 0.2                    # ...and not trusted
    assert r7["brier_mix"] <= r7["brier_self"] + 0.01
    assert r7["brier_model"] > r7["brier_self"]


def test_at_most_one_consult_per_push(monkeypatch):
    person, model = _world(monkeypatch)
    srv = _seat()
    _run(srv, person, 5 * SL.MEET_EVERY)
    pushes = _pushes(srv)
    assert len(pushes) == 5
    assert len(model.asks) == len(pushes)
    assert sorted(a["t"] for a in model.asks) == [p["ts"] for p in pushes]


class _Pool:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


def test_no_consult_when_the_llm_cap_is_spent(monkeypatch):
    person = _Person()
    monkeypatch.setattr(SL, "_read_entity", person.read)
    monkeypatch.setattr(SL, "_push_chat", person.push)
    srv = _seat()
    srv.events_pool = _Pool()
    now = time.time()
    LLM._calls[:] = [(now, "self")] * LLM.CALLER_PER_HOUR   # self's hour is spent
    _run(srv, person, 25)
    assert len(_pushes(srv)) == 1
    assert srv.events_pool.items == []                  # nothing reached the model
    assert _pushes(srv)[0].get("asked") is False
    assert not _pairs(srv)


def test_a_real_consult_is_queued_capped_and_never_runs_a_cycle(monkeypatch):
    person = _Person()
    monkeypatch.setattr(SL, "_read_entity", person.read)
    monkeypatch.setattr(SL, "_push_chat", person.push)
    srv = _seat()
    srv.events_pool = _Pool()
    _run(srv, person, 2)
    assert len(srv.events_pool.items) == 1              # queued, async: the cycle didn't wait
    ev = srv.events_pool.items[0]
    assert ev["caller"] == "self" and ev["entity"] == RESP and ev["readonly"] is True
    rt = SL._rt(srv)
    rt["wake"].clear()
    cycles = sum(1 for r in _record(srv) if r["kind"] == "cycle")
    # the answer lands -- on the test's clock: the reply check pairs only an
    # answer that landed after the push and by the look (#217)
    monkeypatch.setattr(LLM, "time", types.SimpleNamespace(time=lambda: person.t + 1.0))
    LLM.answered(srv, ev, "0.8")
    assert not rt["wake"].is_set()                      # ...and wakes nothing
    assert sum(1 for r in _record(srv) if r["kind"] == "cycle") == cycles
    _run(srv, person, 20, t0=1000.0 + 2 * SL.PACE_S)
    first = [r for r in _looks(srv) if r["ts"] > _pushes(srv)[0]["ts"]][0]
    assert abs(first["p_llm"] - 0.8) < 1e-6             # the real answer pairs at the reply check
    assert len(srv.events_pool.items) == 1              # still one consult for one push
