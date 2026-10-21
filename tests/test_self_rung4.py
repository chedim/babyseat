"""#161 RUNG 4: patience -- choosing WHEN to look.

Self fits each thing's rate of change in time (P(changed | dt) = 1 - exp(-lambda dt),
lambda by maximum likelihood over its recent looks) and looks again when a change
has become likely (60%); when nothing is due it waits. The rung passes when
its timed looks find news (at least half the time, or 0.15 more often than its
fixed-pace looks did -- whichever is easier) and its predictions are
calibrated (when it says 60%, about 60% of those looks find news).
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from _self_cap import cap  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Clock:
    """Things that change on their own, at a true rate, in the fake clock's time."""
    def __init__(self, rates, seed=7):
        self.rates, self.rng, self.last = rates, random.Random(seed), {}

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > __import__("time").time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        lam = self.rates.get(name)
        if lam is None:
            return {"fired": True, "state": None, "changed": None}          # silent
        prev = self.last.get(name)
        self.last[name] = now
        if prev is None:
            return {"fired": True, "state": {"v": 0}, "changed": True}
        changed = self.rng.random() < 1.0 - math.exp(-lam * (now - prev))
        return {"fired": True, "state": {"v": 1}, "changed": changed}


@pytest.fixture(autouse=True)
def _clean():
    WA._getter_paused.clear()


def _seat(names, limit=4, passed=(1, 2, 3)):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    v = np.random.default_rng(1).normal(size=DIM)
    srv.efield = _World(v / np.linalg.norm(v))
    est._srv = srv
    for name in names:
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:" + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    SL.bind(srv)
    from tests._self_cap import world
    world(srv)                              # #337: what it is handed to look at is in its world
    cap(srv, 1)
    for r in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    cap(srv, limit)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _run(srv, n, t0=1000.0):
    for i in range(n):
        SL.cycle(srv, now=t0 + i * SL.PACE_S)
    return t0 + n * SL.PACE_S


def _reaches(srv, **kw):
    return [r for r in _record(srv) if r["kind"] == "reach" and all(r.get(k) == v for k, v in kw.items())]


def test_it_recovers_each_things_rate(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Clock({"fast": 0.05, "slow": 0.004}))
    srv = _seat(("fast", "slow"), limit=3)               # fixed pace: varied gaps from the random choice
    _run(srv, 300)
    recs = _record(srv)
    fast = SL._lam(recs, MEMBRANE_NS + "Component:fast")
    slow = SL._lam(recs, MEMBRANE_NS + "Component:slow")
    assert 0.025 <= fast <= 0.1
    assert 0.002 <= slow <= 0.008


def test_patience_finds_news_more_often_than_the_fixed_pace(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Clock({"slow1": 0.004, "slow2": 0.006}))
    srv = _seat(("slow1", "slow2"), limit=3)
    t = _run(srv, 200)                                    # rung-3 era: looks at a fixed pace
    fixed = _reaches(srv)
    fixed_news = sum(r["answer"] == "changed" for r in fixed[5:]) / len(fixed[5:])
    cap(srv, 4)
    _run(srv, 600, t0=t)
    timed = _reaches(srv, timed=True)
    assert len(timed) >= 30
    news = sum(r["answer"] == "changed" for r in timed) / len(timed)
    assert news >= fixed_news + 0.15


def test_it_waits_when_nothing_is_due(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Clock({"slow1": 0.004}))
    srv = _seat(("slow1",), limit=3)
    t = _run(srv, 60)
    cap(srv, 4)
    n_before = len(_reaches(srv))
    _run(srv, 60, t0=t)
    looks = len(_reaches(srv)) - n_before
    assert looks < 20                                     # it would have looked 60 times at the fixed pace
    assert [r for r in _record(srv) if r["kind"] == "wait"]


@pytest.mark.parametrize("step", [10.0, 1.0])
def test_a_waiting_stretch_is_recorded_once_per_wait_every_seconds_343(monkeypatch, step):
    monkeypatch.setattr(SL, "_read_entity", _Clock({"slow1": 0.004}))
    srv = _seat(("slow1",), limit=3)
    t = _run(srv, 60)
    cap(srv, 4)
    for i in range(int(3 * SL.WAIT_EVERY / step)):
        SL.cycle(srv, now=t + i * step)
    waits = [r["ts"] for r in _record(srv) if r["kind"] == "wait"]
    assert len(waits) >= 2
    assert all(b - a >= SL.WAIT_EVERY for a, b in zip(waits, waits[1:]))   # 300 s apart at 10 s or 1 s cycles


def test_rung_four_opens_by_its_test(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Clock({"fast": 0.03, "slow": 0.004}))
    srv = _seat(("fast", "slow"), limit=3)
    t = _run(srv, 200)
    cap(srv, 4)
    _run(srv, 900, t0=t)
    assert 4 in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]


def test_the_news_bar_for_a_thing_that_is_already_newsy():
    # fixed-pace news 0.8: patience need only keep its looks news-finding (>= 0.5)
    recs = ([{"kind": "reach", "eid": "a", "answer": "changed", "p": 0.8, "dt": 10.0}] * 8
            + [{"kind": "reach", "eid": "a", "answer": "unchanged", "p": 0.8, "dt": 10.0}] * 2
            + [{"kind": "reach", "eid": "a", "answer": a, "p": 0.6, "dt": 40.0, "timed": True}
               for a in ["changed"] * 6 + ["unchanged"] * 4] * 4)
    out = SL._rung4(recs)
    assert out["crit"]["news"] == 1.0


def test_the_limit_holds_rung_four(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Clock({"slow1": 0.004}))
    srv = _seat(("slow1",), limit=3)
    _run(srv, 200)
    assert not _reaches(srv, timed=True)
    assert not [r for r in _record(srv) if r["kind"] == "wait"]
