"""#212: self's curiosity -- choose where to practise by LEARNING PROGRESS.

The three rules from #161 ("self sees its own bar"):
  1. steer by the rise, not the level: prefer the things whose prediction error
     has been falling fastest lately; drop the mastered (flat, low error) and
     the unlearnable (flat, high error) -- but keep an exploration floor, so a
     "can't learn" thing is still retried now and then;
  2. the audit stays blind: progress reads notice's recorded gaps on ORDINARY
     looks only -- a look inside a rung-2 or rung-5 audit (a paused source)
     never feeds it;
  3. seeing never drives the loop: progress is read when self chooses a reach;
     reading it never wakes a cycle, writes nothing, and is never an answer.

THE MEASUREMENT: a world with one mastered thing (always changes: predicted
perfectly, flat), one learnable thing still improving (alternates, and its
noise fades as self practises on it), and one pure-noise thing (a fair coin).
The curious chooser must spend clearly more reaches on the learnable one than
the kind-weighted chooser does.
"""
import json
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
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

DIM = 16
NS = MEMBRANE_NS + "Component:"
LEARN_K = 300          # looks at the learnable thing over which its noise fades (still improving all run)
CYCLES = 900
SEEDS = (3, 11, 29)
MARGIN = 0.10          # mean over the seeds: curious share of reaches on the learnable thing - the base's
MARGIN_EACH = 0.07     # ...and no seed under this


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Three:
    """mastered: always changes. learnable: alternates changed / unchanged,
    with noise that fades over its first LEARN_K looks (practice pays there).
    noise: a fair coin. Honors the audits' pause like the real getter path."""
    def __init__(self, seed=3):
        self.rng = random.Random(seed)
        self.looks = {}

    def __call__(self, srv, ent, now):
        if WA._getter_paused.get(ent.eid, 0) > __import__("time").time():
            return {"fired": True, "state": None, "changed": False, "paused": True}
        name = ent.eid.split(":")[-1]
        k = self.looks[name] = self.looks.get(name, 0) + 1
        if name == "mastered":
            changed = True
        elif name == "learnable":
            noise = 0.5 * max(0.0, 1.0 - k / LEARN_K)
            changed = (k % 2 == 0) if self.rng.random() >= noise else (self.rng.random() < 0.5)
        else:
            changed = self.rng.random() < 0.5
        return {"fired": True, "state": {"v": k}, "changed": changed}


@pytest.fixture(autouse=True)
def _clean():
    WA._getter_paused.clear()


def _seat(names=("mastered", "learnable", "noise"), limit=3, passed=(1, 2)):
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
        e = QuantumEntity(eid=NS + name, cls="Component",
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


def _share(srv, name):
    rs = [r for r in _record(srv) if r["kind"] == "reach"]
    return sum(r["eid"] == NS + name for r in rs) / len(rs), len(rs)


def _looks(eid, errs, **tag):
    """Answered reach records whose Brier error is errs[i] (answer changed, p = 1 - sqrt(err))."""
    return [{"kind": "reach", "eid": eid, "answer": "changed", "p": 1.0 - e ** 0.5, "ts": float(i), **tag}
            for i, e in enumerate(errs)]


# ---- rule 1: the rise, not the level ------------------------------------------

def test_progress_is_the_fall_of_the_error_not_its_level():
    W = SL.CURIO_WINDOW
    recs = (_looks("mastered", [0.0] * (2 * W))                      # flat, perfect
            + _looks("noise", [0.25] * (2 * W))                      # flat, hopeless
            + _looks("learnable", [0.3] * W + [0.1] * W))            # falling
    lp = SL._progress(recs)
    assert lp["learnable"] == pytest.approx(0.2, abs=1e-6)
    assert abs(lp["mastered"]) < 1e-9 and abs(lp["noise"]) < 1e-9


def test_a_thing_without_enough_looks_has_no_progress_yet():
    recs = _looks("young", [0.25] * (2 * SL.CURIO_WINDOW - 1))
    assert "young" not in SL._progress(recs)


def test_the_tilt_favours_the_frontier_and_keeps_a_floor():
    W = SL.CURIO_WINDOW
    recs = (_looks("mastered", [0.0] * (2 * W)) + _looks("noise", [0.25] * (2 * W))
            + _looks("learnable", [0.3] * W + [0.1] * W))
    tilt = SL._curiosity(recs, ["learnable", "mastered", "noise", "young"])
    assert tilt["learnable"] == pytest.approx(1.0)
    assert tilt["young"] == pytest.approx(1.0)                       # unknown: worth a look
    assert tilt["mastered"] == pytest.approx(SL.CURIO_FLOOR)         # dropped, but never to zero
    assert tilt["noise"] == pytest.approx(SL.CURIO_FLOOR)
    assert SL.CURIO_FLOOR > 0


def test_no_progress_anywhere_leaves_the_base_chooser_alone():
    W = SL.CURIO_WINDOW
    recs = _looks("a", [0.0] * (2 * W)) + _looks("b", [0.25] * (2 * W))
    assert SL._curiosity(recs, ["a", "b"]) == {"a": 1.0, "b": 1.0}


def test_curious_self_spends_clearly_more_reaches_on_the_learnable_thing(monkeypatch):
    """THE MEASUREMENT (#212): the same world, the same seeds; the kind-weighted
    chooser (rung 3) against the same chooser tilted by learning progress.
    Measured at the time of writing: base 0.307 on every seed (the base chooser
    doesn't read the world), curious 0.43-0.47 -- a mean margin of about +0.14."""
    margins = []
    for seed in SEEDS:
        monkeypatch.setattr(SL, "CURIOSITY", False)
        monkeypatch.setattr(SL, "_read_entity", _Three(seed))
        base = _seat()
        _run(base, CYCLES)
        base_share, _ = _share(base, "learnable")

        monkeypatch.setattr(SL, "CURIOSITY", True)
        monkeypatch.setattr(SL, "_read_entity", _Three(seed))
        cur = _seat()
        _run(cur, CYCLES)
        cur_share, _ = _share(cur, "learnable")
        print(f"\n#212 seed {seed}: learnable share base {base_share:.3f}, curious {cur_share:.3f}, "
              f"margin {cur_share - base_share:+.3f}")
        margins.append(cur_share - base_share)
        # the exploration floor: the mastered and the noise thing are still retried
        for name in ("mastered", "noise"):
            assert _share(cur, name)[0] > 0.05
    print(f"#212 mean margin {sum(margins) / len(margins):+.3f}")
    assert sum(margins) / len(margins) >= MARGIN
    assert min(margins) >= MARGIN_EACH


def test_the_tilt_also_steers_rung_four_patience(monkeypatch):
    """At rung 4 the tilt picks among the things that are due -- never makes a
    thing due (patience still decides WHEN)."""
    W = SL.CURIO_WINDOW
    recs = (_looks(NS + "mastered", [0.0] * (2 * W)) + _looks(NS + "noise", [0.25] * (2 * W))
            + _looks(NS + "learnable", [0.3] * W + [0.1] * W))
    srv = _seat(limit=4, passed=(1, 2, 3))
    targets = [NS + n for n in ("learnable", "mastered", "noise")]
    picks = [SL._due(srv, recs, targets, {}, 5000.0, random.Random(i))[0] for i in range(300)]
    assert picks.count(NS + "learnable") / len(picks) > 0.6
    assert picks.count(NS + "mastered") > 0 and picks.count(NS + "noise") > 0


# ---- rule 2: the audit stays blind ---------------------------------------------

def test_audit_looks_contribute_nothing_to_progress():
    W = SL.CURIO_WINDOW
    plain = (_looks("a", [0.3] * W + [0.1] * W) + _looks("b", [0.25] * (2 * W)))
    # the same record, with wild looks inside a rung-2 audit and both phases of
    # a rung-5 audit (a paused source, then its restart) interleaved
    noisy = list(plain)
    noisy[W:W] = _looks("b", [0.0] * W, audit=True)
    noisy[3:3] = _looks("a", [1.0] * W, audit5="pause")
    noisy += _looks("b", [1.0, 0.0] * W, audit5="resume") + _looks("c", [1.0] * W + [0.0] * W, audit=True)
    assert SL._progress(noisy) == SL._progress(plain)
    assert SL._curiosity(noisy, ["a", "b", "c"]) == SL._curiosity(plain, ["a", "b", "c"])


def test_status_shows_curiosity_for_the_operator(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Three())
    srv = _seat()
    _run(srv, 300)
    cur = SL.status(srv)["curiosity"]
    assert isinstance(cur, list) and 0 < len(cur) <= 3
    assert {"eid", "progress", "error"} <= set(cur[0])
    assert [c["progress"] for c in cur] == sorted((c["progress"] for c in cur), reverse=True)


# ---- rule 3: seeing never drives the loop ----------------------------------------

def test_reading_progress_never_wakes_a_cycle_writes_nothing_and_is_no_answer(monkeypatch):
    monkeypatch.setattr(SL, "_read_entity", _Three())
    srv = _seat()
    _run(srv, 200)
    rt = SL._rt(srv)
    rt["wake"].clear()
    d = SL._dir(srv)
    before = {p.name: p.stat().st_mtime_ns for p in d.iterdir()}
    n_recs = len(SL._read(srv))
    tempo = json.dumps(SL._tempo(srv), sort_keys=True)
    recs = SL._read(srv)
    targets = SL._targets(srv)
    for _ in range(5):
        SL._progress(recs)
        SL._curiosity(recs, targets)
        SL.status(srv)
    assert not rt["wake"].is_set()                                   # never triggers a try
    assert {p.name: p.stat().st_mtime_ns for p in d.iterdir()} == before   # nothing written
    assert len(SL._read(srv)) == n_recs                              # not on the record
    assert json.dumps(SL._tempo(srv), sort_keys=True) == tempo       # not in self's state
    # never a body answer: the record holds no progress lines, only reaches answer
    kinds = {r["kind"] for r in _record(srv)}
    assert not any("curio" in k or "progress" in k for k in kinds)
    assert all("curiosity" not in r and "progress" not in r for r in _record(srv))
