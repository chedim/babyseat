"""#333 part 3a: NUDGE-AWARE LOOKS.

Each look records `nudged: k` -- Blurp's own nudges at that thing since its
previous look at it (it knows what it did) -- and `p_n`, its nudge-aware
prediction:
  - k = 0: the tempo prediction it makes today (p_n == p);
  - k >= 1: the thing's own Jeffreys-smoothed changed share over its earlier
    looks in the same k bin (NUDGE_K_BINS = (1, 2): k = 1 and k >= 2 -- two
    toggles of a tower block undo each other), prequential: 0.5 before any.

A thing nudged since its last look joins the look candidates, weighed like a
due thing: max(INFO_FLOOR, info(p_n)) x tilt -- an unsure outcome gets looked
at, a known one only now and then; no forced look.

Nudged looks (k >= 1) are kept out of rungs 2-5, the tempo, the rate (lambda)
fits and the look curiosity, as audits are: the earned skills keep measuring
each thing's own tempo, and a nudge cannot make patience slip."""
import json
import random
import time
import types

import numpy as np
import pytest

from membrane import self_loop as SL
from membrane import self_sheet as SH
from membrane import watch_agents as WA
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity

from tests._self_cap import world

DIM = 16
NS = MEMBRANE_NS + "Component:"
A, B = NS + "a", NS + "b"
T0 = 1_700_000_000.0


class _Field:
    def __init__(self):
        v = np.random.default_rng(1).normal(size=DIM)
        self.cur = v / np.linalg.norm(v)

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Script:
    """The thing's answers, in order, one per look (a still `unchanged` after)."""

    def __init__(self):
        self.q = {}

    def __call__(self, srv, ent, now):
        q = self.q.get(ent.eid) or []
        ch = q.pop(0) if q else False
        return {"fired": True, "state": {"v": 1}, "changed": bool(ch)}


@pytest.fixture
def seat(tmp_path, monkeypatch):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    WA._getter_paused.clear()
    script = _Script()
    monkeypatch.setattr(SL, "_read_entity", script)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(tmp_path / "entities.json"), shared=False)
    srv._models_path = str(tmp_path / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _Field()
    est._srv = srv
    for name in ("a", "b"):
        e = QuantumEntity(eid=NS + name, cls="Component",
                          properties={"name": name, "getter_script": "result = {'state': {'x': 1}}"})
        est.docs[e.eid] = e
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    world(srv)
    SL.set_limit(srv, 1)
    for r in (1, 2, 3):
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": r, "skill": 0.9})
    SL.set_limit(srv, 4)                       # look, kinds, patience: open
    srv.script = script
    return srv


def _recs(srv):
    return SL._read(srv)


def _looks(srv, eid=None):
    return [r for r in _recs(srv) if r.get("kind") == "reach" and (eid is None or r["eid"] == eid)]


def _nudge(srv, eid, now, times=1):
    for i in range(times):
        SL._nudge_once(srv, 0, now + i * 0.01, eid, np.zeros(DIM))


def _look(srv, eid, now, changed):
    """One look at `eid` (the only candidate), answering `changed`."""
    srv.script.q[eid] = [changed]
    others = [e for e in (A, B) if e != eid]
    keep = {e: srv.entity_store.docs.pop(e) for e in others}
    try:
        SL._reach_once(srv, srv.entity_store, SL._entity(srv), 0, now)
    finally:
        srv.entity_store.docs.update(keep)
    return _looks(srv, eid)[-1]


# ---- every look carries nudged and p_n --------------------------------------------------

def test_an_unnudged_look_records_nudged_0_and_p_n_is_its_tempo_prediction(seat):
    r = _look(seat, A, T0, True)
    assert r["nudged"] == 0 and r["p_n"] == r["p"]


def test_a_look_records_its_own_nudges_since_its_last_look_at_that_thing(seat):
    _look(seat, A, T0, False)
    _nudge(seat, A, T0 + 1)
    _nudge(seat, B, T0 + 2, times=3)                   # another thing's nudges are not A's
    assert _look(seat, A, T0 + 10, True)["nudged"] == 1
    _nudge(seat, A, T0 + 11, times=2)
    assert _look(seat, A, T0 + 20, False)["nudged"] == 2
    assert _look(seat, A, T0 + 30, False)["nudged"] == 0
    assert _look(seat, B, T0 + 40, True)["nudged"] == 3


def test_the_count_survives_a_restart(seat):
    _look(seat, A, T0, False)
    _nudge(seat, A, T0 + 1)
    SL._RT.pop(id(seat), None)
    SL.bind(seat)
    world(seat)
    assert _look(seat, A, T0 + 10, True)["nudged"] == 1


def test_p_n_is_prequential_per_bin(seat):
    """No look's p_n reads itself or a later look: each is the Jeffreys share
    of the earlier nudged looks in its own k bin (k = 1, k >= 2)."""
    rng = random.Random(5)
    t = T0
    _look(seat, A, t, False)
    for i in range(40):
        t += 20
        k = rng.choice([0, 1, 1, 2, 3])
        if k:
            _nudge(seat, A, t, times=k)
        _look(seat, A, t + 5, rng.random() < (0.9 if k == 1 else 0.2 if k >= 2 else 0.4))
    seen = {1: [0, 0], 2: [0, 0]}
    looks = _looks(seat, A)[1:]
    assert {r["nudged"] for r in looks} >= {0, 1, 2, 3}
    for r in looks:
        k = r["nudged"]
        if k == 0:
            assert r["p_n"] == r["p"]
            continue
        b = 1 if k == 1 else 2
        c, n = seen[b]
        assert r["p_n"] == pytest.approx((c + 0.5) / (n + 1.0), abs=1e-4)
        if r["answer"] in ("changed", "unchanged"):
            seen[b][0] += int(r["answer"] == "changed")
            seen[b][1] += 1
    assert seen[1][1] > 5 and seen[2][1] > 5


def test_the_bins_are_the_approved_ones():
    assert SL.NUDGE_K_BINS == (1, 2)
    assert [SL._k_bin(k) for k in (0, 1, 2, 3, 7)] == [None, 1, 2, 2, 2]


# ---- the check: a nudged thing joins the candidates -------------------------------------

def _quiet(eid, n=40, dt=600.0, t0=T0 - 40 * 600.0):
    """Looks that taught it the thing hardly changes: its rate is tiny, so
    patience's wait is long."""
    return [{"ts": t0 + i * dt, "kind": "reach", "eid": eid, "answer": "unchanged", "p": 0.1, "dt": dt}
            for i in range(n)]


def test_a_thing_nudged_since_its_last_look_joins_the_candidates(seat):
    recs = _quiet(A)
    SL._tempo(seat)[A] = {"changed": 0, "answered": 40, "last": T0}
    pick = SL._due(seat, recs, [A], {}, T0 + 60, random.Random(1))
    assert pick == (None, None)                                   # not due: it waits
    _nudge(seat, A, T0 + 30)
    assert SL._due(seat, recs, [A], {}, T0 + 60, random.Random(1))[0] == A


def test_an_unsure_nudged_thing_is_checked_more_than_a_known_one(seat):
    recs = _quiet(A) + _quiet(B)
    for e in (A, B):
        SL._tempo(seat)[e] = {"changed": 0, "answered": 40, "last": T0}
        _nudge(seat, e, T0 + 30)
    st = SL._nudge_state(seat)
    st[A]["bins"] = {"1": [20, 20]}                               # a nudge always moved it: known
    st[B]["bins"] = {"1": [10, 20]}                               # half the time: unsure
    picks = [SL._due(seat, recs, [A, B], {}, T0 + 60, random.Random(i))[0] for i in range(600)]
    na, nb = picks.count(A), picks.count(B)
    p_a = (20 + 0.5) / 21
    expect = max(SL.INFO_FLOOR, SL._info(p_a)) / (max(SL.INFO_FLOOR, SL._info(p_a)) + SL._info(10.5 / 21))
    assert nb > 2 * na and na > 0
    assert na / 600 == pytest.approx(expect, abs=0.05)


def test_a_silent_thing_nudged_is_a_candidate_too(seat):
    recs = _quiet(A)
    SL._tempo(seat)[A] = {"changed": 0, "answered": 40, "last": T0}
    _nudge(seat, A, T0 + 30)
    picks = {SL._due(seat, recs, [A], {A: "silent"}, T0 + 60, random.Random(i))[0] for i in range(50)}
    assert picks == {A}


# ---- kept out of rungs 2-5, the tempo, lambda and curiosity ------------------------------

def test_a_nudged_look_teaches_the_tempo_nothing_but_when_it_looked(seat):
    _look(seat, A, T0, False)
    _look(seat, A, T0 + 10, False)
    before = json.loads(json.dumps(SL._tempo(seat)[A]))
    _nudge(seat, A, T0 + 15)
    r = _look(seat, A, T0 + 20, True)
    after = SL._tempo(seat)[A]
    assert r["nudged"] == 1
    assert {k: v for k, v in after.items() if k != "last"} == {k: v for k, v in before.items() if k != "last"}
    assert after["last"] == T0 + 20                       # the next look's interval starts here
    assert "timed" not in r and "steer" not in r and "watch5" not in r


def test_a_nudged_look_never_triggers_a_shift(seat, monkeypatch):
    called = []
    monkeypatch.setattr(SL, "_shift_check", lambda *a, **k: called.append(a))
    SL._append(seat, {"ts": 2.0, "kind": "rung", "rung": 5, "skill": 1.0})
    SL._RT.pop(id(seat), None)
    SL.bind(seat)
    world(seat)
    SL.set_limit(seat, 5)
    _look(seat, A, T0, False)
    n0 = len(called)
    _nudge(seat, A, T0 + 5)
    _look(seat, A, T0 + 10, True)
    assert len(called) == n0


def _mixed():
    """Ordinary looks at A and B, with nudged looks between them (all found news)."""
    rng = random.Random(3)
    out, t = [], T0
    for i in range(300):
        for e in (A, B):
            t += 10
            out.append({"ts": t, "kind": "reach", "eid": e, "answer": rng.choice(["changed", "unchanged"]),
                        "p": round(rng.random(), 3), "dt": 10.0 + rng.random() * 50, "nudged": 0,
                        "timed": i > 100, "steer": True, "watch5": i > 150})
            if rng.random() < 0.4:
                t += 1
                out.append({"ts": t, "kind": "reach", "eid": e, "answer": "changed", "p": 0.2, "dt": 1.0,
                            "nudged": rng.choice([1, 2]), "p_n": 0.9})
    return out


def _ordinary_only(recs):
    return [r for r in recs if not r.get("nudged")]


@pytest.mark.parametrize("reader", [
    lambda r: SL._rung2_stats(r, {}), lambda r: SL._rung2(r, {}), lambda r: SL._kinds(r),
    lambda r: SL._kind_rate(r), lambda r: SL._rung3(r, {}), lambda r: SL._rung4(r, {}),
    lambda r: SL._rung5(r, {}), lambda r: SL._lam(r, A), lambda r: SL._lam(r, B),
    lambda r: SL._ordinary_errors(r), lambda r: SL._progress(r, detail=True),
    lambda r: SL._curiosity(r, [A, B]), lambda r: [r[i] for i in SL._rate_looks(r)],
    lambda r: SH._curiosity_v(r)])
def test_nudged_looks_never_reach_the_rung_2_to_5_readers_lambda_or_curiosity(reader):
    recs = _mixed()
    assert sum(1 for r in recs if r.get("nudged")) > 100
    assert reader(recs) == reader(_ordinary_only(recs))


def test_consolidation_keeps_the_ordinary_windows_whatever_nudged_looks_lie_between(tmp_path, monkeypatch):
    """The summary's windows are counted in ordinary looks, so after a sleep the
    readers still see as many as before."""
    from unittest import mock
    from membrane import energy as E
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path))
    srv = types.SimpleNamespace(entity_store=EntityStore(path=str(tmp_path / "entities.json"), shared=False),
                                _models_path=str(tmp_path / "models.json"), paused=False, efield=None)
    p = SL.record_path(srv)
    p.parent.mkdir(parents=True, exist_ok=True)
    recs = [{"ts": T0 - 1, "kind": "birth", "by": "operator"}] + _mixed()
    p.write_text("".join(json.dumps(r) + "\n" for r in recs))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    readers = (lambda r: SL._rung2_stats(r, {}), lambda r: SL._kinds(r), lambda r: SL._rung3(r, {}),
               lambda r: SL._rung4(r, {}), lambda r: SL._lam(r, A), lambda r: SL._lam(r, B),
               lambda r: SL._progress(r, detail=True))
    before = [f(SL._read(srv)) for f in readers]
    with mock.patch.object(E, "_avail_ram", return_value=None):
        assert SL.consolidate(srv, recs[-1]["ts"] + 30) is not None
    after = [f(SL._read(srv)) for f in readers]
    assert SL._read(srv)[0]["kind"] == "summary"
    assert after == before
