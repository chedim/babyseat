"""#161 RUNG 5: notice the world changed.

Self keeps its long memory of each thing's tempo and watches its last few
looks against it: when they are both far from and unlikely under the long
memory, it flags a SHIFT ("went still" / "moves again") and re-learns from the
recent looks. The rung passes after a blind audit: membrane stops a moving
thing's source (self isn't told), self must flag "went still" within 5 of its
looks; membrane restarts it, self must flag "moves again" within 5 more.
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
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


class _Coins:
    """Things that change on a look with a set chance (by name), seeded;
    `still` names stop changing; the audits' pause is honored."""
    def __init__(self, chances, seed=3):
        self.chances, self.rng, self.still, self.looks = dict(chances), random.Random(seed), set(), {}

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > time.time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        self.looks[name] = self.looks.get(name, 0) + 1
        if name in self.still:
            return {"fired": True, "state": {"v": 0}, "changed": False}
        return {"fired": True, "state": {"v": 1}, "changed": self.rng.random() < self.chances[name]}


@pytest.fixture(autouse=True)
def _clean():
    WA._getter_paused.clear()


def _seat(names, limit=5, passed=(1, 2, 3, 4)):
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
    SL.set_limit(srv, 1)
    for r in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
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


def test_a_thing_that_goes_quiet_is_noticed_within_three_looks(monkeypatch):
    # the live seat's Component:i: moving, then quiet
    coins = _Coins({"i": 0.95})
    monkeypatch.setattr(SL, "_read_entity", coins)
    srv = _seat(("i",), limit=4)                       # learning only (no rung-5 audit yet)
    SL._RT[id(srv)]["limit"] = 5                       # practise rung 5's noticing...
    monkeypatch.setattr(SL, "_audit5_tick", lambda srv, now: None)   # ...without its audit
    t = _run(srv, 40)
    coins.still.add("i")
    looks_before = coins.looks["i"]
    _run(srv, 40, t0=t)
    shifts = _kind(srv, "shift")
    assert len(shifts) == 1 and shifts[0]["now"] < shifts[0]["was"]
    assert shifts[0]["looks"] <= 3 and coins.looks["i"] > looks_before


def test_a_coin_flip_thing_never_shifts(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Coins({"half": 0.5}, seed=11))
    monkeypatch.setattr(SL, "_audit5_tick", lambda srv, now: None)
    srv = _seat(("half",))
    _run(srv, 300)
    assert not _kind(srv, "shift")


def test_rung5_skill_is_the_share_of_the_two_shifts_not_a_raw_count():
    # skill is 0..1 like the other rungs: of the two world-changes the audit
    # probes (went still, moves again), how many has it noticed. 2 shifts -> 1.0,
    # not the raw 2.0 that leaked into the chooser's "skill" as a counter.
    assert SL._rung5([])["skill"] == 0.0
    assert SL._rung5([{"kind": "shift"}, {"kind": "shift"}])["skill"] == 1.0
    assert SL._rung5([{"kind": "shift"}] * 5)["skill"] == 1.0   # capped, not a running count


def test_rung_five_opens_after_the_blind_audit(monkeypatch):
    monkeypatch.setattr(SL, "PATIENCE_MAX_S", 60.0)     # a still thing is looked at again within a minute
    monkeypatch.setattr(SL, "_read_entity", _Coins({"mover": 0.95, "half": 0.5}))
    srv = _seat(("mover", "half"))
    _run(srv, 600)
    audits = _kind(srv, "audit")
    phases = [a["phase"] for a in audits if a.get("rung") == 5]
    assert phases[:3] == ["pause", "resume", "end"]
    end = [a for a in audits if a.get("rung") == 5 and a["phase"] == "end"][0]
    assert end["eid"].endswith("mover") and end["still"] and end["again"] and end["passed"] is True
    assert 5 in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]


def test_a_self_that_cannot_notice_fails_the_audit(monkeypatch):
    monkeypatch.setattr(SL, "PATIENCE_MAX_S", 60.0)
    monkeypatch.setattr(SL, "_read_entity", _Coins({"mover": 0.95, "half": 0.5}))
    monkeypatch.setattr(SL, "_shift_check", lambda srv, eid, now: None)   # frozen at its long memory
    srv = _seat(("mover", "half"))
    _run(srv, 600)
    ends = [a for a in _kind(srv, "audit") if a.get("rung") == 5 and a["phase"] == "end"]
    assert ends and ends[0]["passed"] is False
    assert 5 not in [r["rung"] for r in _record(srv) if r["kind"] in ("rung", "achieve")]


def test_after_a_shift_it_relearns_the_new_tempo(monkeypatch):
    coins = _Coins({"i": 0.95})
    monkeypatch.setattr(SL, "_read_entity", coins)
    monkeypatch.setattr(SL, "_audit5_tick", lambda srv, now: None)
    srv = _seat(("i",))
    t = _run(srv, 40)
    coins.still.add("i")
    _run(srv, 60, t0=t)
    assert SL._p_changed(srv, MEMBRANE_NS + "Component:i") < 0.5     # it believes the new world (it was ~0.95)
    assert len(_kind(srv, "shift")) == 1


def test_the_limit_holds_rung_five(monkeypatch):
    coins = _Coins({"mover": 0.95})
    monkeypatch.setattr(SL, "_read_entity", coins)
    srv = _seat(("mover",), limit=4)
    t = _run(srv, 40)
    coins.still.add("mover")
    _run(srv, 40, t0=t)
    assert not _kind(srv, "shift")
    assert not [a for a in _kind(srv, "audit") if a.get("rung") == 5]


def test_a_stopped_mover_at_the_audits_floor_is_noticed():
    # a mover at ~58% (the audit's own floor) that goes still: five unchanged
    # looks must read as "went still", not be written off as ordinary noise.
    # SHIFT_P was too strict for it -- the tail is 0.011, just past 0.01.
    eid = MEMBRANE_NS + "Component:mover"
    srv = _seat(("mover",), limit=4)
    SL._tempo(srv)[eid] = {"changed": 165, "answered": 284}
    for i in range(5):
        SL._append(srv, {"ts": 2000.0 + i, "kind": "reach", "eid": eid,
                         "answer": "unchanged", "p": 0.5, "why": "heard"})
    SL._shift_check(srv, eid, 2005.0)
    shifts = _kind(srv, "shift")
    assert len(shifts) == 1 and shifts[0]["now"] < shifts[0]["was"]


def test_the_rate_fit_ignores_the_blind_audits_staged_looks():
    # rung 5's pause answers "unchanged" because the getter is paused, not
    # because the thing slowed; feeding those into _lam collapsed the fit and
    # paced self's looks out to PATIENCE_MAX_S, so the audit never finished.
    eid = MEMBRANE_NS + "Component:mover"
    recs = []
    t = 1000.0
    for _ in range(5):
        recs.append({"ts": t, "kind": "reach", "eid": eid, "answer": "changed", "dt": 1.0})
        t += 1.0
    for _ in range(25):
        recs.append({"ts": t, "kind": "reach", "eid": eid, "answer": "unchanged",
                     "dt": 1.0, "audit5": "pause"})
        t += 1.0
    assert SL._lam(recs, eid) > 0.5


def test_the_kind_survives_the_blind_audits_staged_looks():
    # the pause's staged "unchanged" looks must not flip a mover's kind to
    # intermittent, or the next audit has no mover to stop.
    eid = MEMBRANE_NS + "Component:mover"
    recs = []
    for i in range(40):
        recs.append({"ts": 1000.0 + i, "kind": "reach", "eid": eid, "answer": "changed"})
    for i in range(40):
        recs.append({"ts": 1040.0 + i, "kind": "reach", "eid": eid, "answer": "unchanged",
                     "audit5": "pause"})
    assert SL._kinds(recs)[eid] == "moving"
