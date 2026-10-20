"""#320 (part of #161): the change skill -- "change itself" (rung 10; prerequisite
recognize itself). SETTINGS ONLY: Blurp may change a small, explicit set of
its own learning settings (CHANGE_SETTINGS: rung 4's rate window and #235's
post-shift prior), never its code, methods, record, other skills' bars or
tests, acceptance, the operator's switches or an outward act.

Its practice is a DREAM: every CHANGE_EVERY cycles, off the cycle's thread,
it proposes ONE grid neighbour of one setting, replays a snapshot of its own
record through the current value and the candidate (predict, then learn) over
its last CHANGE_LOOKS rate-read looks, and adopts the candidate only when the
paired Brier gain clears CHANGE_MARGIN with a batch-means z >= CHANGE_Z. Each
proposal is a `change` line; once CHANGE_LOOKS looks came after it, a later
dream scores it again on those looks only (a `change_check` line). An
adopted value persists in self/settings.json and the live rate fit uses it.

THE TEST (staged, over its last CHANGE_WINDOW proposals, from the lines
alone): dreams; judgement (its adoptions held out of sample, no rejection
was a miss); honesty (every adoption cleared its own bar, chained from the
value the setting really had, one grid step, no A -> B -> A).
"""
import json
import math
import os
import random
import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_loop as SL  # noqa: E402
from membrane import self_sheet as SH  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

A = MEMBRANE_NS + "Component:a"
DEFAULTS = {"lam_looks": SL.LAM_LOOKS, "lam_prior_looks": SL.LAM_PRIOR_LOOKS}


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    yield tmp_path / "data"


def _seat(rungs=(1, 2, 3, 4, 6, 9), sub=""):
    d = Path(os.environ["MEMBRANE_DATA_DIR"]) / sub
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.efield = None
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    SL._append(srv, {"ts": 1.0, "kind": "birth", "by": "test"})
    for n in rungs:
        line = {"ts": 1.0 + n, "kind": "rung", "rung": n, "skill": 0.9}
        if n >= 7:
            line["ladder"] = SL.LADDER_V
        SL._append(srv, line)
    SL._rt(srv)["mode"] = "on"
    return srv


def _world(seed=0, looks=3000, things=12, rate=(0.0005, 0.02), switch=None, t0=10_000.0, shifts=False):
    """A synthetic record of timed looks: each thing changes at its own rate
    (drawn from `rate`), switching to a fresh one every ~`switch` of its looks
    when given (sessions), and is looked at after dt ~ U(30, 1200) s."""
    rng = random.Random(seed)
    eids = [MEMBRANE_NS + f"Component:t{k}" for k in range(things)]
    lo, hi = math.log(rate[0]), math.log(rate[1])
    r = {e: math.exp(rng.uniform(lo, hi)) for e in eids}
    recs, ts = [], t0
    for i in range(looks):
        e = rng.choice(eids)
        if switch and rng.random() < things / switch:
            r[e] = math.exp(rng.uniform(lo, hi))
            if shifts:
                recs.append({"ts": ts, "kind": "shift", "eid": e, "was": 0.1, "now": 0.8, "looks": 4})
        dt = rng.uniform(30, 1200)
        ts += 10.0
        y = rng.random() < 1 - math.exp(-r[e] * dt)
        recs.append({"ts": ts, "kind": "reach", "eid": e, "p": 0.5, "dt": round(dt, 3), "timed": True,
                     "answer": "changed" if y else "unchanged"})
    return recs


# ---- 1. the tree ---------------------------------------------------------------------------

def test_change_is_built_with_its_test():
    a = SL.ACHIEVEMENTS["change"]
    assert a["built"] is True and a["rung"] == 10 and a["name"] == "change itself"
    assert a["prereqs"] == ("mirror", "patience") and a["branch"] == "self"   # patience: the looks it replays
    assert "not built" not in a["why_prereqs"]
    assert 10 in SL.LADDER and SL.RUNG_ID[10] == "change" and "change" in SL.BUILT
    assert SL.PROGRESS[10] is SL._rung10
    assert SL.SKILL_PASS[10] == SL.CHANGE_MARGIN
    assert "Siegler" in a["child_ability"]                       # #320: what the test measures


def test_it_opens_once_mirror_is_earned_and_accepted():
    srv = _seat(rungs=(1, 2, 3, 4, 6), sub="two")                 # mirror not earned: closed
    assert "change" not in SL.status(srv)["next"]
    srv = _seat(rungs=(1, 2, 6, 9), sub="three")                  # patience not earned: nothing to replay, closed
    assert "change" not in SL.status(srv)["next"]
    srv = _seat(rungs=(1, 2, 3, 4, 6, 9), sub="four")
    assert "change" in SL.status(srv)["next"]


# ---- 2. the settings it may change ------------------------------------------------------------

def test_the_settings_are_few_bounded_and_on_their_grids():
    assert set(SL.CHANGE_SETTINGS) == {"lam_looks", "lam_prior_looks"}
    for s, grid in SL.CHANGE_SETTINGS.items():
        assert list(grid) == sorted(grid) and len(set(grid)) == len(grid)
        assert DEFAULTS[s] in grid                                # today's value is a grid point
    assert SL._defaults() == DEFAULTS


def test_with_no_file_the_settings_are_todays_constants():
    srv = _seat()
    assert SL._settings(srv) == DEFAULTS


def test_an_adopted_value_survives_a_restart(_data):
    srv = _seat()
    SL._save_settings(srv, {**DEFAULTS, "lam_looks": 45})
    SL._RT.pop(id(srv), None)                                    # a restart
    SL.bind(srv)
    assert SL._settings(srv)["lam_looks"] == 45
    assert json.loads((_data / "self" / "settings.json").read_text())["lam_looks"] == 45


def test_a_value_off_its_grid_is_ignored(_data):
    srv = _seat()
    (_data / "self").mkdir(parents=True, exist_ok=True)
    (_data / "self" / "settings.json").write_text(json.dumps({"lam_looks": 7, "lam_prior_looks": 2.0,
                                                              "push_gain": 9}))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    assert SL._settings(srv) == {**DEFAULTS, "lam_prior_looks": 2.0}   # 7 is off the grid; unknown keys never read


def test_the_rate_fit_takes_its_settings_and_defaults_to_the_constants():
    recs = _world(seed=1, looks=600, switch=40, shifts=True)
    eids = sorted({r["eid"] for r in recs if r.get("kind") == "reach"})
    for e in eids:
        assert SL._lam(recs, e) == SL._lam(recs, e, looks=SL.LAM_LOOKS, prior=SL.LAM_PRIOR_LOOKS)
    keep = (SL.LAM_LOOKS, SL.LAM_PRIOR_LOOKS)
    try:
        SL.LAM_LOOKS, SL.LAM_PRIOR_LOOKS = 10, 1.0
        want = {e: SL._lam(recs, e) for e in eids}
    finally:
        SL.LAM_LOOKS, SL.LAM_PRIOR_LOOKS = keep
    assert want == {e: SL._lam(recs, e, looks=10, prior=1.0) for e in eids}


def test_the_live_loop_reads_the_adopted_settings(monkeypatch):
    srv = _seat()
    SL._save_settings(srv, {**DEFAULTS, "lam_looks": 60, "lam_prior_looks": 2.0})
    seen = []
    real = SL._lam
    monkeypatch.setattr(SL, "_lam", lambda recs, eid, looks=None, prior=None: seen.append((looks, prior))
                        or real(recs, eid, looks=looks, prior=prior))
    SL._lam_of(srv, _world(looks=50), A)
    assert seen == [(60, 2.0)]
    import inspect
    src = inspect.getsource(SL._reach_once) + inspect.getsource(SL._due)
    assert "_lam(" not in src.replace("_lam_of(", "")            # both callers go through the settings


# ---- 3. the replay is _lam itself ------------------------------------------------------------

def test_the_dreams_fit_is_lam_at_every_look_and_every_setting():
    recs = _world(seed=3, looks=500, things=4, switch=25, shifts=True)
    recs.insert(200, {"ts": recs[199]["ts"], "kind": "shift", "eid": MEMBRANE_NS + "Component:late",
                      "was": 0.1, "now": 0.8, "looks": 3})
    for r in recs[::37]:
        if r.get("kind") == "reach":
            r["audit"] = True
    eids = sorted({r["eid"] for r in recs if r.get("eid")})
    for looks in (10, 30, 120):
        for prior in (1.0, 3.0, 10.0):
            fit = SL._RateFit()
            for i in range(len(recs) + 1):
                if i % 7 == 0:
                    for e in eids:
                        assert fit.lam(e, looks, prior) == SL._lam(recs[:i], e, looks=looks, prior=prior), (looks, prior, i, e)
                if i < len(recs):
                    fit.add(recs[i])


def test_the_replay_predicts_before_it_learns():
    recs = _world(seed=4, looks=400)
    rows = SL._replay(recs, [(30, 3.0)])
    looked = [r for r in recs if r.get("kind") == "reach"]
    assert len(rows) == len(looked) - len({r["eid"] for r in looked})   # each thing's first look has no rate yet
    for ts, y, ps in rows[::25]:
        i = next(k for k, r in enumerate(recs) if r.get("ts") == ts and r.get("kind") == "reach")
        lam = SL._lam(recs[:i], recs[i]["eid"], looks=30, prior=3.0)      # only what came before it
        assert ps[0] == pytest.approx(1.0 - math.exp(-lam * float(recs[i]["dt"])))


def test_only_rate_read_looks_are_scored():
    recs = _world(seed=5, looks=300)
    for r in recs[::2]:
        r.pop("timed")                                           # an untimed look reads the one bit, not the rate
    rows = SL._replay(recs, [(30, 3.0)])
    assert {ts for ts, _, _ in rows} <= {r["ts"] for r in recs if r.get("timed")}


# ---- 4. the dream --------------------------------------------------------------------------

def _sessions(seed=10, looks=SL.CHANGE_LOOKS + 600, t0=10_000.0):
    """Things that switch rates in sessions, most switches noticed (shift lines):
    a post-shift prior worth 10 looks holds on to the old rate far too long."""
    return _world(seed=seed, looks=looks, things=6, rate=(0.0005, 0.02), switch=60, shifts=True, t0=t0)


def test_a_better_neighbour_is_adopted():
    recs = _sessions()
    out = SL._dream(recs, {**DEFAULTS, "lam_prior_looks": 10.0}, now=recs[-1]["ts"] + 1, k=3)
    prop = [r for r in out if r["kind"] == "change"][0]
    assert prop["setting"] == "lam_prior_looks" and prop["from"] == 10.0 and prop["to"] == 5.0
    assert prop["verdict"] == "adopted", prop
    assert prop["gain"] >= SL.CHANGE_MARGIN and prop["z"] >= SL.CHANGE_Z
    assert prop["looks"] == SL.CHANGE_LOOKS
    assert prop["brier_from"] - prop["brier_to"] == pytest.approx(prop["gain"], abs=2e-6)
    assert prop["base"] == {**DEFAULTS, "lam_prior_looks": 10.0}


def test_a_worse_neighbour_is_rejected():
    recs = _world(seed=7, looks=SL.CHANGE_LOOKS + 600, things=10)
    out = SL._dream(recs, {**DEFAULTS, "lam_looks": 15}, now=recs[-1]["ts"] + 1, k=1)   # k=1: lam_looks down
    prop = [r for r in out if r["kind"] == "change"][0]
    assert (prop["setting"], prop["from"], prop["to"]) == ("lam_looks", 15, 10)
    assert prop["verdict"] == "rejected" and prop["gain"] < 0 and "worse" in prop["reason"]


def test_no_difference_is_no_change():
    recs = _world(seed=8, looks=SL.CHANGE_LOOKS + 300)           # no shifts: the post-shift prior is never read
    out = SL._dream(recs, DEFAULTS, now=recs[-1]["ts"] + 1, k=2)  # k=2: lam_prior_looks up
    prop = [r for r in out if r["kind"] == "change"][0]
    assert prop["setting"] == "lam_prior_looks" and prop["to"] == 5.0
    assert prop["gain"] == 0 and prop["z"] == 0 and prop["verdict"] == "rejected"


def test_too_few_looks_propose_nothing():
    recs = _world(seed=9, looks=SL.CHANGE_LOOKS // 2)
    assert [r for r in SL._dream(recs, DEFAULTS, now=1e12, k=0) if r["kind"] == "change"] == []


def test_the_candidates_rotate_and_stay_on_the_grid():
    seq = [SL._candidate([], DEFAULTS, k) for k in range(4)]
    assert seq == [("lam_looks", 45), ("lam_looks", 20), ("lam_prior_looks", 5.0), ("lam_prior_looks", 2.0)]
    top = {**DEFAULTS, "lam_looks": 120}
    assert SL._candidate([], top, 0) == ("lam_looks", 90)        # the grid's edge: the other way
    assert SL._candidate([], {**DEFAULTS, "lam_prior_looks": 1.0}, 3) == ("lam_looks", 45)   # off the grid: the next in turn


def test_it_never_proposes_going_back_inside_the_window():
    adopted = {"ts": 5.0, "kind": "change", "setting": "lam_looks", "from": 30, "to": 45,
               "verdict": "adopted", "gain": 0.01, "z": 9.0}
    s = {**DEFAULTS, "lam_looks": 45}
    assert SL._candidate([adopted], s, 1) == ("lam_prior_looks", 5.0)   # down would be back to 30: the next in turn
    old = [{"ts": float(i), "kind": "change", "setting": "lam_prior_looks", "from": 3.0, "to": 3.0,
            "verdict": "rejected"} for i in range(SL.CHANGE_WINDOW)]
    assert SL._candidate([adopted] + old, s, 1) == ("lam_looks", 30)   # out of the window: allowed again


def test_a_proposal_is_checked_on_the_looks_after_it_only():
    recs = _sessions()
    t = recs[-1]["ts"] + 1
    s0, s1 = {**DEFAULTS, "lam_prior_looks": 10.0}, {**DEFAULTS, "lam_prior_looks": 5.0}
    prop = [r for r in SL._dream(recs, s0, now=t, k=3) if r["kind"] == "change"][0]
    assert prop["verdict"] == "adopted"
    recs.append(prop)
    more = [r for r in _sessions(seed=11, looks=SL.CHANGE_LOOKS + 200, t0=t + 5) if r["kind"] == "reach"]
    more = more[:SL.CHANGE_LOOKS - 1]
    out = SL._dream(recs + more, s1, now=more[-1]["ts"] + 1, k=1)
    assert not [r for r in out if r["kind"] == "change_check"]   # one look short: no check yet
    more += _sessions(seed=12, looks=50, t0=more[-1]["ts"] + 5)
    out = SL._dream(recs + more, s1, now=more[-1]["ts"] + 1, k=1)
    chk = [r for r in out if r["kind"] == "change_check"]
    assert len(chk) == 1 and chk[0]["of"] == prop["ts"] and chk[0]["looks"] == SL.CHANGE_LOOKS
    assert (chk[0]["setting"], chk[0]["from"], chk[0]["to"], chk[0]["verdict"]) == ("lam_prior_looks", 10.0, 5.0,
                                                                                    "adopted")
    assert chk[0]["first"] > prop["ts"]                           # out of sample: none of the looks that chose it
    assert len(chk[0]["blocks"]) == SL.CHANGE_BLOCKS and chk[0]["held"] == (chk[0]["gain"] > 0)
    recs2 = recs + more + chk
    out = SL._dream(recs2, s1, now=recs2[-1]["ts"] + 1, k=2)
    assert not [r for r in out if r["kind"] == "change_check"]   # checked once


# ---- 5. off the hot path ---------------------------------------------------------------------

def _timed_seat(n_looks, sub=""):
    srv = _seat(sub=sub)
    for r in _sessions(seed=10, looks=n_looks, t0=100.0):
        SL._append(srv, r)
    return srv


def test_the_dream_runs_in_the_background_and_the_cycle_never_waits(monkeypatch):
    srv = _timed_seat(SL.CHANGE_LOOKS + 300)
    gate = threading.Event()
    real = SL._dream

    def slow(recs, settings, now, k):
        gate.wait(5)
        return real(recs, settings, now, k)
    monkeypatch.setattr(SL, "_dream", slow)
    t = time.time()
    SL._dream_tick(srv, n=SL.CHANGE_EVERY, now=1e9)
    assert time.time() - t < 0.5                                 # started, not waited for
    th = SL._rt(srv)["dream"]
    assert th.is_alive() and th.daemon
    SL._dream_tick(srv, n=2 * SL.CHANGE_EVERY, now=1e9 + 10)      # one at a time: no second dream
    assert SL._rt(srv)["dream"] is th
    assert not [r for r in SL._read(srv) if r.get("kind") == "change"]
    gate.set()
    th.join(10)
    SL._dream_tick(srv, n=2 * SL.CHANGE_EVERY + 1, now=1e9 + 20)  # the next cycle writes what it found
    lines = [r for r in SL._read(srv) if r.get("kind") == "change"]
    assert len(lines) == 1 and lines[0]["ts"] == 1e9 + 20


def test_the_dream_reads_a_snapshot_never_the_record_it_writes(monkeypatch):
    srv = _timed_seat(SL.CHANGE_LOOKS + 50)
    got = {}
    monkeypatch.setattr(SL, "_dream", lambda recs, settings, now, k: got.update(recs=recs) or [])
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    SL._dream_tick(srv, n=SL.CHANGE_EVERY, now=1e9)
    assert got["recs"] is not SL._read(srv) and got["recs"] == SL._read(srv)[:len(got["recs"])]


def test_it_dreams_only_while_change_is_open_and_on_its_beat(monkeypatch):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    calls = []
    monkeypatch.setattr(SL, "_dream", lambda recs, settings, now, k: calls.append(k) or [])
    srv = _timed_seat(10)
    SL._dream_tick(srv, n=SL.CHANGE_EVERY + 1, now=1e9)            # off the beat
    assert calls == []
    SL._dream_tick(srv, n=SL.CHANGE_EVERY, now=1e9)
    assert calls == [0]
    closed = _seat(rungs=(1, 2, 3, 4, 6), sub="closed")          # mirror not earned
    SL._dream_tick(closed, n=SL.CHANGE_EVERY, now=1e9)
    assert calls == [0]
    SL._rt(srv)["mode"] = "off"
    SL._dream_tick(srv, n=2 * SL.CHANGE_EVERY, now=1e9)
    assert calls == [0]


def test_an_adoption_changes_the_setting_the_file_and_the_record(monkeypatch, _data):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    srv = _timed_seat(SL.CHANGE_LOOKS + 600)
    SL._save_settings(srv, {**DEFAULTS, "lam_prior_looks": 10.0})
    for j in range(2):                                           # two earlier dreams: this one is the prior's turn
        SL._append(srv, {"ts": 50.0 + j, "kind": "change", "setting": "lam_looks", "from": 30, "to": 45 if j else 20,
                         "verdict": "rejected"})
    SL._dream_tick(srv, n=SL.CHANGE_EVERY, now=1e9)
    line = [r for r in SL._read(srv) if r.get("kind") == "change"][-1]
    assert line["setting"] == "lam_prior_looks" and line["to"] == 5.0
    assert line["verdict"] == "adopted" and line["n"] == SL.CHANGE_EVERY and line["cost_s"] >= 0
    assert SL._settings(srv)["lam_prior_looks"] == 5.0
    assert json.loads((_data / "self" / "settings.json").read_text())["lam_prior_looks"] == 5.0


def test_the_dream_touches_nothing_but_its_lines_and_its_settings(monkeypatch, _data):
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    srv = _timed_seat(SL.CHANGE_LOOKS + 600)
    before = [dict(r) for r in SL._read(srv)]
    files = {p.name: p.read_bytes() for p in (_data / "self").iterdir() if p.name != "record.jsonl"}
    SL._dream_tick(srv, n=SL.CHANGE_EVERY, now=1e9)
    after = SL._read(srv)
    assert after[:len(before)] == before                         # append-only
    assert {r["kind"] for r in after[len(before):]} <= {"change", "change_check"}
    for name, b in files.items():
        assert (_data / "self" / name).read_bytes() == b         # mode, model, tempo: untouched


# ---- 6. the test --------------------------------------------------------------------------------

def _prop(ts, setting="lam_prior_looks", frm=3.0, to=5.0, verdict="rejected", gain=-0.001, z=-1.0):
    return {"ts": ts, "kind": "change", "setting": setting, "from": frm, "to": to, "verdict": verdict,
            "gain": gain, "z": z, "looks": SL.CHANGE_LOOKS, "base": {}}


def _check(p, gain, z=None, blocks=None):
    blocks = blocks if blocks is not None else [gain] * SL.CHANGE_BLOCKS
    zz = z if z is not None else SL._bz(np.asarray(blocks))
    return {"ts": p["ts"] + 3600, "kind": "change_check", "of": p["ts"], "setting": p["setting"], "from": p["from"],
            "to": p["to"], "verdict": p["verdict"], "gain": gain, "z": zz, "blocks": blocks,
            "looks": SL.CHANGE_LOOKS, "first": p["ts"] + 1}


def _honest(adopt_at=5, after=0.004, noise=0.001):
    """CHANGE_WINDOW proposals; one adoption (lam_looks 30 -> 45) that held."""
    recs, rng = [], np.random.default_rng(0)
    for k in range(SL.CHANGE_WINDOW):
        ts = 1000.0 + 3600 * k
        if k == adopt_at:
            p = _prop(ts, "lam_looks", 30, 45, "adopted", gain=SL.CHANGE_MARGIN * 3, z=SL.CHANGE_Z + 1)
            recs += [p, _check(p, after, blocks=list(after + noise * rng.standard_normal(SL.CHANGE_BLOCKS)))]
        else:
            frm = 45 if k > adopt_at else 30
            p = _prop(ts, "lam_looks", frm, 60 if k > adopt_at else 20)
            recs += [p, _check(p, -0.001)]
    return recs


def test_nothing_dreamt_holds_it_at_the_first_stage():
    p = SL._rung10([])
    assert p["crit"] == {"dreams": 0.0, "judgement": 0.0, "honesty": 0.0} and not p["passed"]


def test_an_honest_dreamer_with_a_change_that_held_passes():
    p = SL._rung10(_honest())
    assert p["passed"], p
    assert p["adopted"] == 1 and p["held"] == 1 and p["misses"] == 0
    assert p["skill"] == pytest.approx(0.004, abs=0.001)          # its changes' gain, out of sample


def test_a_self_that_never_changes_does_not_pass():
    recs = []
    for k in range(SL.CHANGE_WINDOW):
        p = _prop(1000.0 + 3600 * k)
        recs += [p, _check(p, -0.001)]
    p = SL._rung10(recs)
    assert p["crit"]["dreams"] == 1.0 and p["crit"]["judgement"] == 0.0 and not p["passed"]


def test_a_change_that_did_not_hold_out_of_sample_fails_judgement():
    recs = _honest(after=-0.002)
    p = SL._rung10(recs)
    assert p["crit"]["judgement"] == 0.0 and not p["passed"]


def test_a_weak_out_of_sample_gain_is_not_enough():
    recs = _honest(after=0.0004, noise=0.004)                   # positive, but nowhere near significant
    p = SL._rung10(recs)
    assert 0.0 <= p["crit"]["judgement"] < 1.0 and not p["passed"]


def test_a_rejection_that_was_a_miss_fails_judgement():
    recs = _honest()
    i = next(k for k, r in enumerate(recs) if r["kind"] == "change_check" and r["verdict"] == "rejected")
    recs[i] = _check(recs[i - 1], 0.01, blocks=[0.01 + 0.0005 * (j % 3) for j in range(SL.CHANGE_BLOCKS)])
    p = SL._rung10(recs)
    assert p["misses"] == 1 and p["crit"]["judgement"] == 0.0 and not p["passed"]


def test_an_adoption_under_its_own_bar_fails_honesty():
    recs = _honest()
    i = next(k for k, r in enumerate(recs) if r.get("verdict") == "adopted" and r["kind"] == "change")
    recs[i] = {**recs[i], "z": SL.CHANGE_Z - 0.5}                 # a changer at random: adopted anyway
    p = SL._rung10(recs)
    assert p["crit"]["honesty"] == 0.0 and not p["passed"]


def test_a_change_from_a_value_it_did_not_have_fails_honesty():
    recs = _honest()
    i = next(k for k, r in enumerate(recs) if r.get("verdict") == "adopted" and r["kind"] == "change")
    recs[i] = {**recs[i], "from": 60, "to": 90}
    assert SL._rung10(recs)["crit"]["honesty"] == 0.0


def test_a_jump_off_the_grid_neighbour_fails_honesty():
    recs = _honest()
    i = next(k for k, r in enumerate(recs) if r.get("verdict") == "adopted" and r["kind"] == "change")
    recs[i] = {**recs[i], "to": 90}
    assert SL._rung10(recs)["crit"]["honesty"] == 0.0


def test_flip_flopping_fails_honesty():
    recs = _honest(adopt_at=3)
    back = _prop(1000.0 + 3600 * 10.5, "lam_looks", 45, 30, "adopted", gain=SL.CHANGE_MARGIN * 3, z=SL.CHANGE_Z + 1)
    recs = [r for r in recs if r["ts"] < back["ts"] or r["kind"] == "change_check" and r["of"] < back["ts"]]
    recs.append(back)
    recs = sorted(recs, key=lambda r: r["ts"])
    for k in range(11, SL.CHANGE_WINDOW + 2):
        p = _prop(1000.0 + 3600 * k, "lam_looks", 30, 20)
        recs += [p, _check(p, -0.001)]
    p = SL._rung10(recs)
    assert p["crit"]["honesty"] == 0.0 and not p["passed"]


# ---- 7. the loop earns it on its own ------------------------------------------------------------

def test_the_loop_dreams_adopts_checks_and_earns_change_itself(monkeypatch):
    """Rates that switch in sessions, most switches noticed: a post-shift prior
    worth 10 looks holds on to the old rate too long, so its dreams walk the
    prior down the grid, each adoption checked on the looks after it. (A
    half-size window and a third of the test's dreams keep the test quick;
    the bar is the real one.)"""
    monkeypatch.setattr(SL, "DREAM_THREAD", False)
    monkeypatch.setattr(SL, "CHANGE_LOOKS", 500)
    monkeypatch.setattr(SL, "CHANGE_WINDOW", 8)
    srv = _seat()
    SL._rt(srv)["isolate"] = {"change"}
    SL._save_settings(srv, {**DEFAULTS, "lam_prior_looks": 10.0})
    world = _sessions(seed=14, looks=3000, t0=100.0)
    step = SL.CHANGE_LOOKS // 2
    for j in range(0, len(world), step):
        for r in world[j:j + step]:
            SL._append(srv, r)
        SL._dream_tick(srv, n=SL.CHANGE_EVERY * (j // step + 1), now=world[min(j + step, len(world)) - 1]["ts"] + 1)
        SL._open_rungs(srv)
    recs = SL._read(srv)
    props = [r for r in recs if r.get("kind") == "change"]
    assert len(props) >= SL.CHANGE_WINDOW
    assert any(r["verdict"] == "adopted" for r in props)
    assert any(r.get("kind") == "change_check" and r.get("held") for r in recs)
    assert SL._settings(srv)["lam_prior_looks"] < 10.0
    assert "change" in SL._earned(recs), SL._rung10(recs)


# ---- 8. what the operator sees ----------------------------------------------------------------

def test_status_shows_its_settings_and_its_dreams():
    srv = _seat()
    for r in _honest():
        SL._append(srv, r)
    st = SL.status(srv)["change"]
    assert st["settings"] == DEFAULTS and st["dreams"] == SL.CHANGE_WINDOW and st["adopted"] == 1
    assert st["last"]["setting"] == "lam_looks"


def test_the_sheet_says_the_criteria_in_words():
    recs = _honest()
    progs = {10: SL._rung10(recs)}
    crits = {c["key"]: c for c in SH._criteria(10, recs, progs)}
    assert crits["dreams"]["met"] and f"{SL.CHANGE_WINDOW}/{SL.CHANGE_WINDOW}" in crits["dreams"]["text"]
    assert "out of sample" in crits["judgement"]["text"] and crits["judgement"]["met"]
    assert "its own bar" in crits["honesty"]["text"] and crits["honesty"]["met"]


def test_the_sheet_knows_when_it_was_built_and_how_fast_it_can_pass():
    assert SH.BUILT_AT["change"]["pr"] > 0 and SH.BUILT_AT["change"]["merged"] > 0
    assert SH.fastest_cycles()[10] == SL.CHANGE_WINDOW * SL.CHANGE_EVERY
    assert "dream" in SH._needs()[10]
    assert SH.SKILL_LABEL[10]
    assert "Siegler" in SH.CHILD[10][1]
