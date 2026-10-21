"""#346: status() -- the panel's state, which the cycle's _show reads every
cycle -- read the whole record some 25 times a call: _earn_order rebuilt the
earn times once per achievement, rung 2's stats were rebuilt for the split
and for each rung test that reads them (and grouped the looks by thing with
a scan per thing: time ~ looks x things), rung 8's series refitted the
advisor's calibration over every check before each one (quadratic in the
checks). One status() call now shares what it reads (a per-call scope, gone
when the call returns) and none of those parts grows faster than the record.

Its numbers must not change: the outputs below were pinned from the old
code, and verbatim copies of the old helpers are compared with the new ones
on many random records.
"""
import json
import math
import os
import random
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane.entities import MEMBRANE_NS  # noqa: E402
from tests._self_cap import rebuilt  # noqa: E402

C = MEMBRANE_NS + "Component:"
ALICE = C + "chat:in:alice"
BOB = C + "chat:in:bob"
T0 = 1_760_000_000.0
NOW = T0 + 10_000_000.0
TEMPO = {ALICE: {"changed": 7, "answered": 9}, C + "w:t0": {"changed": 1, "answered": 30},
         C + "w:t1": {"changed": 20, "answered": 21}}


# ---- the old code, verbatim (#346: what the new must equal) ---------------------------

def _old_earn_order(recs):
    return [i for i in SL.ACHIEVEMENTS if i in SL._earn_times(recs)]


def _old_rung2_stats(recs, tempo):
    ans = [r for r in recs if r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
           and not r.get("audit")]
    if not ans:
        return {}
    pool = ans[-SL.RUNG2_REACHES * 4:]
    p0 = sum(r["answer"] == "changed" for r in pool) / len(pool)
    out = {}
    for eid in sorted({r["eid"] for r in ans}):
        mine = [r for r in ans if r["eid"] == eid][-SL.RUNG2_REACHES:]
        a = [1.0 if r["answer"] == "changed" else 0.0 for r in mine]
        brier = sum((x - r["p"]) ** 2 for x, r in zip(a, mine))
        base = sum((x - p0) ** 2 for x in a)
        skill = (1.0 - brier / base) if base > 1e-9 else 0.0
        t = tempo.get(eid) or {}
        p_hat = (float(t.get("changed", 0)) + 0.5) / (float(t.get("answered", 0)) + 1.0)
        out[eid] = {"reaches": len(mine), "skill": round(skill, 4), "p": round(p_hat, 3),
                    "candidate": len(mine) >= SL.RUNG2_REACHES and skill >= SL.RUNG2_SKILL,
                    "auditable": p_hat >= SL.AUDIT_P}
    return out


def _old_rung8_series(recs, eid):
    """The pre-#346 _rung8_series, verbatim but for its p_self cache (it recomputes)."""
    rows = SL._rung8_rows(recs, eid)
    n0, k0 = SL._r8_carry(recs, eid)
    off = X = y = None
    k = float(k0)
    for i, r in enumerate(rows):
        r["rate"] = (k + 0.5) / (n0 + i + 1.0)
        if r["rec_p_self"] is not None:
            r["p_self"] = float(r["rec_p_self"])
        else:
            if off is None:
                off, X = SL._r8_design(rows, n0, k0)
                y = SL.np.array([x["o"] for x in rows], dtype=float)
            r["p_self"] = round(SL._r8_predict(off, X, y, i), 4)
        if r["p_llm"] is None:
            r["cal"], r["w"], r["p"] = (0.0, 0.0), 0.0, r["p_self"]
        else:
            rc = r["rec_cal"]
            r["cal"] = (float(rc[0]), float(rc[1])) if isinstance(rc, (list, tuple)) and len(rc) == 2 \
                else SL._advisor_cal(rows[:i], r["model"])
            r["w"] = r["cal"][1]
            r["p"] = (float(r["rec_p"]) if r["rec_p"] is not None
                      else SL._advise(r["p_self"], float(r["p_llm"]), r["cal"]))
        k += r["o"]
    return rows


# ---- random records: every kind status() reads -----------------------------------------

def _world(seed, n, summary=False, things=6, voids=True):
    """A random waking record: cycles (pushed, still, steered), pushes (asked
    or not, to alice or bob, chat or the ball), looks at both and at
    `things` words (answered or not, audits, steered, timed, noticing, reply
    checks with or without the advisor's answer and calibration), audits,
    shifts, mirror feeds, dreams, sleeps that drop a turn, rung / achieve
    passes and their voids (#254's and #333's), accepts, the migration line,
    unlocks -- optionally after a #345 summary line."""
    rng = random.Random(seed)
    recs = []
    if summary:
        recs.append({"kind": SL.SUMMARY, "id": "s", "at": T0, "n": 0,
                     "carry": {"cycle": 40, "push": 9, "change": 2, "change_check": 1,
                               "fixed": {C + "w:t0": [12, 3]},
                               "to": {ALICE: {"asked": 7, "checked": 5, "paired": 2, "aim": 3},
                                      BOB: {"asked": 3, "checked": 3, "paired": 1}},
                               "r8": {SL._r8_key(ALICE, "chat", 1): [11, 4],
                                      SL._r8_key(BOB, "chat", 1): [6, 1]}},
                     "stalls": []})
    if rng.random() < 0.8:
        recs.append({"ts": T0, "kind": "birth", "by": "operator"})
    words = [C + f"w:t{j}" for j in range(things)]
    ts = T0 + 10
    asked_ts = []
    ids = [i for i, a in SL.ACHIEVEMENTS.items() if a["rung"] in SL.PROGRESS]   # a pass status() can read
    for i in range(n):
        ts += rng.uniform(0.5, 30.0) if rng.random() > 0.01 else rng.uniform(700, 2000)   # a stall now and then
        t = ts
        x = rng.random()
        if x < 0.30:
            c = {"ts": t, "kind": "cycle", "n": i, "change": rng.choice(["world", "none"]),
                 "gap": rng.uniform(0, 0.3), "naive": rng.uniform(0.1, 0.5)}
            if rng.random() < 0.3:
                c["still"] = True
            if rng.random() < 0.3:
                c["aim_err"], c["aim_naive"] = rng.uniform(0, 0.4), rng.uniform(0.1, 0.5)
            recs.append(c)
        elif x < 0.40:
            to = rng.choice([ALICE, ALICE, BOB])
            p = {"ts": t, "kind": "push", "n": i, "to": to, "eid": MEMBRANE_NS + "self",
                 "asked": rng.random() < 0.6, "bin": rng.randrange(3)}
            if rng.random() < 0.15:
                p["medium"], p["clock"] = "toy", rng.choice([1, 2])
            if rng.random() < 0.5:
                p["spoke_age"] = rng.uniform(0, 900)
            if rng.random() < 0.6:
                p["aim"] = rng.randrange(4)
            if rng.random() < 0.3:
                p["words"] = rng.choice(["changed", "still", None])
            if p.get("asked"):
                asked_ts.append(float(p["ts"]))
            recs.append(p)
        elif x < 0.85:
            e = rng.choice([ALICE, ALICE, BOB] + words)
            r = {"ts": t, "kind": "reach", "n": i, "eid": e, "p": rng.random(),
                 "answer": rng.choice(["changed", "unchanged", "unchanged", "none"])}
            for k, q in (("audit", 0.05), ("audit5", 0.03), ("steer", 0.4), ("timed", 0.3), ("watch5", 0.3)):
                if rng.random() < q:
                    r[k] = True
            if e in (ALICE, BOB) and rng.random() < 0.5:
                r["reply"] = True
                r["replied"] = rng.random() < 0.35
                if rng.random() < 0.1:
                    r.pop("replied")
                if rng.random() < 0.6:
                    r["p_self"] = round(rng.uniform(0.05, 0.95), 4)
                if rng.random() < 0.6:
                    r["p_llm"] = round(rng.uniform(0.01, 0.99), 3)
                    r["llm_model"] = rng.choice(["m-a", "m-b", None])
                    if rng.random() < 0.3:
                        r["llm_cal"] = [round(rng.uniform(-1, 1), 4), round(rng.uniform(0, 1), 4)]
                        r["p_mix"] = round(rng.uniform(0.05, 0.95), 4)
                if rng.random() < 0.15:
                    r["medium"], r["clock"] = "toy", rng.choice([1, 2])
            recs.append(r)
        elif x < 0.88:
            recs.append({"ts": t, "kind": "mirror", "toy": rng.choice(["mirror", "mirror", "glass2"]),
                         "glass": rng.uniform(-1, 1), "stranger": rng.uniform(-1, 1),
                         "p_glass": rng.uniform(-1, 1), "b_glass": rng.uniform(-1, 1),
                         "p_stranger": rng.uniform(-1, 1), "b_stranger": rng.uniform(-1, 1)})
        elif x < 0.90:
            recs.append({"ts": t, "kind": "audit", "phase": "end", "eid": rng.choice(words + [ALICE]),
                         "rung": rng.choice([2, 5]), "passed": rng.choice([True, False, None])})
        elif x < 0.92:
            recs.append({"ts": t, "kind": "shift", "eid": rng.choice(words)})
        elif x < 0.93:
            recs.append({"ts": t, "kind": "change", "setting": "lam_looks", "from": 30, "to": 45,
                         "verdict": rng.choice(["adopted", "rejected"]), "gain": rng.uniform(-0.01, 0.01),
                         "z": rng.uniform(-3, 5)})
        elif x < 0.945:
            d = {"turn": rng.choice(asked_ts), "to": ALICE} if asked_ts and rng.random() < 0.7 else {}
            recs.append({"ts": t, "kind": "sleep", "dropped": d})
        elif x < 0.96:
            n_ = rng.randrange(1, 12)
            line = {"ts": t, "kind": "rung", "rung": n_, "skill": round(rng.random(), 3)}
            if rng.random() < 0.6:
                line["ladder"] = rng.choice([2, 3])
            recs.append(line)
        elif x < 0.975:
            i_ = rng.choice(ids)
            line = {"ts": t, "kind": "achieve", "id": i_, "skill": round(rng.random(), 3)}
            if rng.random() < 0.7:
                line["rung"] = SL.ACHIEVEMENTS[i_]["rung"]
            if rng.random() < 0.7:
                line["ladder"] = SL.LADDER_V
            recs.append(line)
        elif x < 0.982 and voids:
            v = {"ts": t, "kind": "rung_void", "why": "test"}
            if rng.random() < 0.5:
                v["rung"] = rng.randrange(1, 12)
            else:
                v["id"] = rng.choice(ids)
            if rng.random() < 0.3:
                v["issue"], v["voids_ts"] = 333, t - 5
            recs.append(v)
        elif x < 0.99:
            recs.append({"ts": t, "kind": "accept", "id": rng.choice(ids), "by": "test"})
        elif x < 0.993:
            recs.append({"ts": t, "kind": "accept_migrated", "ids": rng.sample(ids, 3)})
        else:
            recs.append({"ts": t, "kind": "unlock", "rung": rng.randrange(1, 12), "late": rng.random() < 0.5,
                         "toys": rng.sample(["dice", "mirror", "ball", "lamp"], 2)})
    return recs


def _cases():
    return {
        "empty": [],
        "birth_only": [{"ts": T0, "kind": "birth", "by": "operator"}],
        "summary_only": [{"kind": SL.SUMMARY, "id": "s", "at": T0, "n": 0, "stalls": [],
                          "carry": {"cycle": 12, "push": 4, "to": {ALICE: {"asked": 4, "checked": 3, "paired": 1}}}}],
        "summary_then_lines": _world(7, 300, summary=True),
        "voided_rungs": [
            {"ts": T0, "kind": "birth"},
            {"ts": T0 + 1, "kind": "rung", "rung": 1, "skill": 0.9}, {"ts": T0 + 2, "kind": "rung", "rung": 2},
            {"ts": T0 + 3, "kind": "rung_void", "rung": 2, "why": "x"},
            {"ts": T0 + 4, "kind": "achieve", "id": "reach", "rung": 2, "skill": 0.3},
            {"ts": T0 + 5, "kind": "rung", "rung": 6, "skill": 1.0},
            {"ts": T0 + 6, "kind": "rung_void", "rung": 6, "id": "meet", "issue": 333, "voids_ts": T0 + 5},
            {"ts": T0 + 7, "kind": "achieve", "id": "kinds", "skill": 0.4},
            {"ts": T0 + 8, "kind": "rung_void", "id": "kinds", "why": "broken test"},
            {"ts": T0 + 9, "kind": "accept_migrated", "ids": ["feel"]},
            {"ts": T0 + 10, "kind": "accept", "id": "reach"},
            {"ts": T0 + 11, "kind": "unlock", "rung": 2, "toys": ["dice"]},
        ],
        "random_a": _world(1, 600),
        "random_b": _world(2, 600, summary=True),
        "random_c": _world(3, 1500, things=12),
        "random_no_voids": _world(4, 800, voids=False),
        "random_many_things": _world(5, 1200, things=40),
    }


# ---- status() on a seat that holds the record in memory ------------------------------

_SRVS: list = []     # held so a seat's id (the runtime's key) is never reused within the run


def _status(recs, mode="on"):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, _models_path=str(d / "models.json"))
    _SRVS.append(srv)
    rt = SL._rt(srv)
    rt.update(mode=mode, recs=recs, recs_bytes=0, recs_cycles=SL._cycles(recs), tempo=dict(TEMPO))
    with mock.patch.object(SL.time, "time", return_value=NOW), \
            mock.patch.object(TB, "last_person_poke", return_value={"ts": NOW - 42.0}):
        return json.loads(json.dumps(SL.status(srv)))


def _clean(x):
    return json.loads(json.dumps(x))


def _pins(tree):
    out = {}
    for name, recs in _cases().items():
        o = {"status": _status(recs)}
        if name in ("random_a", "voided_rungs"):
            o["status_off"] = _status(recs, mode="off")
        o["earn_order"] = SL._earn_order(recs)
        o["open_ids"] = SL._open_ids(recs)
        o["rung2_stats"] = SL._rung2_stats(recs, TEMPO)
        o["split"] = SL._split(recs, TEMPO)
        o["latest_unlock"] = SL._latest_unlock(recs)
        o["series"] = {e: SL._rung8_series(recs, e) for e in (ALICE, BOB)}
        o["progress"] = {n: SL.PROGRESS[n](recs, TEMPO) for n in SL.PROGRESS}
        out[name] = _clean(o)
    return out


PINNED_PATH = Path(__file__).with_name("data") / "status_scans_346.json"


@pytest.fixture(params=["built", "whole_tree"])
def tree(request, monkeypatch):
    if request.param == "whole_tree":
        rebuilt(monkeypatch)
    return request.param


def _same(a, b):
    """Equal, but an unrounded float (a fitted p_self, the advisor's
    calibration) may differ in its last bits across platforms' BLAS."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9,
                                                                                          abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_status_and_its_helpers_give_the_old_codes_numbers(tree):
    pinned = json.loads(PINNED_PATH.read_text())[tree]
    got = _pins(tree)
    assert set(got) == set(pinned)
    for name in pinned:
        for k in pinned[name]:
            assert _same(got[name][k], pinned[name][k]), (name, k)


@pytest.mark.parametrize("seed", range(30))
def test_the_new_helpers_equal_the_old_on_random_records(seed):
    rng = random.Random(1000 + seed)
    recs = _world(seed + 50, rng.randrange(0, 700), summary=rng.random() < 0.3, things=rng.randrange(1, 30),
                  voids=rng.random() < 0.8)
    tempo = TEMPO if seed % 2 else {}
    assert SL._earn_order(recs) == _old_earn_order(recs)
    assert SL._rung2_stats(recs, tempo) == _old_rung2_stats(recs, tempo)
    for e in (ALICE, BOB):
        assert _clean(SL._rung8_series(recs, e)) == _clean(_old_rung8_series(recs, e))


@pytest.mark.parametrize("seed", range(6))
def test_status_inside_its_scope_equals_its_parts_read_alone(seed):
    """Every number status() shares within its call is the number the
    helper gives read alone, outside any scope."""
    recs = _world(seed + 200, 500, summary=seed % 2 == 0, things=8)
    st = _status(recs)
    assert st["next"] == SL._open_ids(recs)
    assert st["split"] == {e: s for e, s in SL._split(recs, TEMPO).items() if s != "unknown"}
    assert st["rung8"] == _clean(SL._rung8_stats(recs))
    assert st["unlock"] == _clean(SL._latest_unlock(recs))
    assert st["old_wiring"] == _clean(SL._old_wiring(recs))


def test_the_scope_is_gone_when_status_returns():
    recs = _world(9, 200)
    _status(recs)
    assert SL._SCOPE.get() is None
    recs.append({"ts": NOW, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9})
    assert "feel" in SL._earn_order(recs)            # a line appended after the call is read


# ---- the structure: no part of status() grows faster than the record ------------------

class _Line(dict):
    """A record line that counts how often it is read."""
    reads = 0

    def get(self, *a):
        _Line.reads += 1
        return super().get(*a)

    def __getitem__(self, k):
        _Line.reads += 1
        return super().__getitem__(k)


class _Record(list):
    """The record, counting its full passes (a walk back from the end stops
    at the latest line it wants: not counted)."""
    def __init__(self, *a):
        super().__init__(*a)
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        return super().__iter__()


def _counted(n, things):
    return _Record(_Line(r) for r in _world(77, n, things=things))


def _reads(recs):
    _Line.reads = 0
    _status(recs)
    return _Line.reads


def test_status_reads_each_line_a_bounded_number_of_times_as_the_record_grows():
    """At N and 4N lines -- with the things looked at growing with the record,
    as a seat's world does -- the line reads per status() call grow at most
    linearly (generous: 6x for 4x), and stay under a per-line budget."""
    small, big = _counted(1500, 30), _counted(6000, 120)
    a, b = _reads(small), _reads(big)
    assert b <= 6 * a, (a, b)
    assert b / len(big) <= 60, b / len(big)


def test_status_passes_over_the_record_a_bounded_number_of_times():
    """The record is walked a fixed number of times per call: 60 passes
    before #346 (the counting passes alone rebuilt 17 times), 26 now."""
    recs = _counted(2000, 10)
    _status(recs)                                   # the seat's load (its one full count) is the first pass
    assert recs.passes - 1 <= 30, recs.passes - 1


def test_the_advisors_calibration_reads_a_bounded_window_per_check():
    """_rung8_series refits the advisor over its last RUNG8_TRUST checks under
    one model: the rows it hands _advisor_cal per check stay bounded (they were
    every check before it -- quadratic in the checks)."""
    recs = _world(31, 4000, things=1)
    for r in recs:
        r.pop("llm_cal", None)
    seen = []
    real = SL._advisor_cal

    def spy(rows, model):
        seen.append(len(rows))
        return real(rows, model)
    with mock.patch.object(SL, "_advisor_cal", spy):
        SL._rung8_series(recs, ALICE)
    assert seen and max(seen) <= SL.RUNG8_TRUST, max(seen)


if __name__ == "__main__":                       # regenerate the pins (from the OLD code only)
    import pytest as _pt
    out = {}
    for t in ("built", "whole_tree"):
        mp = _pt.MonkeyPatch()
        if t == "whole_tree":
            rebuilt(mp)
        out[t] = _pins(t)
        mp.undo()
    PINNED_PATH.parent.mkdir(exist_ok=True)
    PINNED_PATH.write_text(json.dumps(out, sort_keys=True, indent=0) + "\n")
    print("pinned", PINNED_PATH, PINNED_PATH.stat().st_size)
