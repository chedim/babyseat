"""#346: rung 4's choice (_due) runs every cycle once patience is open, and it
fitted each target's rate (_lam_of -> _lam) with its own pass over the whole
record -- and another, backwards, for the target's last shift: time ~ targets
x record (~0.7 s a cycle on a 16 MB record with 15 targets). The looks _lam
fits on and the last shifts are now read in ONE pass, shared within the call
(the per-call _Scope #397 introduced; nothing kept across calls).

Its numbers must not change: the outputs below were pinned from the old code,
and a verbatim copy of the old code is compared with the new on many random
records (out-of-order and equal stamps, shifts before and after their looks,
summaries, rung and rung_void lines included).
"""
import json
import math
import os
import random
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402
from membrane.entities import MEMBRANE_NS  # noqa: E402

np = SL.np
C = MEMBRANE_NS + "Component:"
T0 = 1_760_000_000.0
SETTINGS = ((None, None), (30, 3.0), (10, 1.0), (120, 10.0))


# ---- the old code, verbatim (#346: what the new must equal) ---------------------------

def _old_last_shift_ts(recs, eid):
    return next((r["ts"] for r in reversed(recs) if r.get("kind") == "shift" and r.get("eid") == eid), None)


def _old_lam(recs, eid, looks=None, prior=None):
    W = SL.LAM_LOOKS if looks is None else int(looks)
    K = SL.LAM_PRIOR_LOOKS if prior is None else float(prior)
    since = _old_last_shift_ts(recs, eid)
    fit = [(r["eid"], r["ts"], float(r["dt"]), r["answer"] == "changed") for r in recs
           if r.get("kind") == "reach" and not r.get("audit") and not r.get("audit5")
           and r.get("answer") in ("changed", "unchanged") and r.get("dt")]
    mine = [o for o in fit if o[0] == eid]
    obs = [o for o in mine if since is None or o[1] > since][-W:]
    if not obs:
        return None
    ll = SL._lam_ll(obs)
    if since is not None:
        old = ([o for o in mine if o[1] <= since] or [o for o in fit if o[1] <= since])[-W:]
        if old:
            ll = ll + SL._lam_prior_ll(eid, old, K)
    return float(SL.LAM_GRID[int(np.argmax(ll))])


def _old_lam_of(srv, recs, eid):
    s = SL._settings(srv)
    return _old_lam(recs, eid, looks=s["lam_looks"], prior=s["lam_prior_looks"])


def _old_due(srv, recs, targets, kinds, now, rng):
    tempo = SL._tempo(srv)
    due = []
    for e in targets:
        if kinds.get(e) == "silent":
            continue
        last = (tempo.get(e) or {}).get("last")
        lam = _old_lam_of(srv, recs, e)
        if last is None or lam is None:
            due.append((e, lam))
            continue
        wait = min(SL.PATIENCE_MAX_S, max(SL.PACE_S, -np.log(1.0 - SL.PATIENCE_P) / lam))
        if now - float(last) >= wait:
            due.append((e, lam))
    silent = [e for e in targets if kinds.get(e) == "silent"]
    if silent and rng.random() < SL.SILENT_WEIGHT:
        return rng.choice(silent), None
    if not due:
        return None, None
    tilt = SL._curiosity(recs, [e for e, _ in due])
    if not SL.ATTENTION:
        return rng.choices(due, weights=[tilt[e] for e, _ in due])[0]
    weights = [max(SL.INFO_FLOOR, SL._info(SL._due_p(srv, tempo, e, lam, now))) * tilt[e] for e, lam in due]
    return rng.choices(due, weights=weights)[0]


# ---- records -------------------------------------------------------------------------

def _things(k):
    return [C + f"w:t{j}" for j in range(k)]


def _world(seed, n, things=6, summary=False, messy=True):
    """A random record of what _due and _lam read: looks (answered or not,
    with or without dt, in audits), shifts -- some stamped before looks that
    come earlier, some at a look's own stamp -- cycles, waits, sleeps, rung
    passes and their voids, optionally after a #345 summary line. `messy`:
    stamps out of order now and then."""
    rng = random.Random(seed)
    words = _things(things)
    recs = []
    if summary:
        recs.append({"kind": SL.SUMMARY, "id": "s", "at": T0, "n": 0, "stalls": [], "carry": {"cycle": 30}})
    recs.append({"ts": T0, "kind": "birth", "by": "operator"})
    ts = T0 + 10
    for i in range(n):
        ts += rng.uniform(0.5, 60.0) if rng.random() > 0.01 else rng.uniform(700, 2000)
        t = ts - rng.uniform(0, 300) if messy and rng.random() < 0.05 else ts
        x = rng.random()
        if x < 0.65:
            e = rng.choice(words)
            r = {"ts": t, "kind": "reach", "n": i, "eid": e, "p": rng.random(),
                 "answer": rng.choice(["changed", "unchanged", "unchanged", "none"])}
            if rng.random() < 0.9:
                r["dt"] = rng.choice([0, rng.uniform(1, 30), rng.uniform(30, 3000), rng.expovariate(1 / 200)])
            for k, q in (("audit", 0.05), ("audit5", 0.03), ("timed", 0.4), ("steer", 0.3)):
                if rng.random() < q:
                    r[k] = True
            recs.append(r)
        elif x < 0.72:
            sh = {"ts": t, "kind": "shift", "eid": rng.choice(words), "was": 0.2, "now": 0.8, "looks": 5}
            if messy and rng.random() < 0.2 and recs and recs[-1].get("ts") is not None:
                sh["ts"] = recs[-1]["ts"]              # at the stamp of the line before it
            recs.append(sh)
        elif x < 0.85:
            recs.append({"ts": t, "kind": "cycle", "n": i, "change": rng.choice(["world", "none"])})
        elif x < 0.89:
            recs.append({"ts": t, "kind": "wait", "n": i})
        elif x < 0.92:
            recs.append({"ts": t, "kind": "sleep", "dropped": {}})
        elif x < 0.96:
            recs.append({"ts": t, "kind": "rung", "rung": rng.randrange(1, 12), "skill": round(rng.random(), 3)})
        else:
            v = {"ts": t, "kind": "rung_void", "why": "test", "rung": rng.randrange(1, 12)}
            if rng.random() < 0.3:
                v["issue"], v["voids_ts"] = 333, t - 5
            recs.append(v)
    return recs


def _tempo_for(recs, seed, things):
    """A tempo with `last` stamps at, before and missing for each thing."""
    rng = random.Random(seed)
    last_ts = max((float(r["ts"]) for r in recs if r.get("ts") is not None), default=T0)
    out = {}
    for e in _things(things):
        x = rng.random()
        if x < 0.15:
            continue
        t = {"changed": rng.randrange(0, 20), "answered": rng.randrange(20, 40)}
        if x > 0.3:
            t["last"] = last_ts - rng.choice([0.0, 5.0, 60.0, 600.0, 7200.0])
        if rng.random() < 0.5:
            t["prev"] = rng.choice(["changed", "unchanged"])
            t["after"] = {"changed": [rng.randrange(0, 5), rng.randrange(5, 10)],
                          "unchanged": [rng.randrange(0, 5), rng.randrange(5, 10)]}
        out[e] = t
    return out


def _kinds_for(seed, things):
    rng = random.Random(seed)
    return {e: rng.choice(["silent", "moving", "steady", "unknown"]) for e in _things(things) if rng.random() < 0.6}


_SRVS: list = []     # held so a seat's id (the runtime's key) is never reused within the run


def _srv(tempo, looks=None, prior=None):
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None)
    _SRVS.append(srv)
    rt = SL._rt(srv)
    rt["tempo"] = tempo
    rt["settings"] = {"lam_looks": SL.LAM_LOOKS if looks is None else looks,
                      "lam_prior_looks": SL.LAM_PRIOR_LOOKS if prior is None else prior}
    return srv


def _cases():
    one = C + "w:t0"
    return {
        "empty": ([], 3),
        "summary_only": ([{"kind": SL.SUMMARY, "id": "s", "at": T0, "n": 0, "stalls": [], "carry": {}}], 3),
        "summary_then_lines": (_world(11, 400, things=4, summary=True), 4),
        "shift_before_any_own_look": ([
            {"ts": T0 + 1, "kind": "reach", "eid": C + "w:t1", "answer": "changed", "dt": 30.0},
            {"ts": T0 + 2, "kind": "reach", "eid": C + "w:t1", "answer": "unchanged", "dt": 300.0},
            {"ts": T0 + 3, "kind": "shift", "eid": one},
            {"ts": T0 + 4, "kind": "reach", "eid": one, "answer": "changed", "dt": 12.0},
            {"ts": T0 + 5, "kind": "reach", "eid": one, "answer": "unchanged", "dt": 40.0}], 2),
        "voided_rungs": ([
            {"ts": T0, "kind": "birth"},
            {"ts": T0 + 1, "kind": "rung", "rung": 4, "skill": 0.9},
            {"ts": T0 + 2, "kind": "reach", "eid": one, "answer": "changed", "dt": 20.0, "timed": True},
            {"ts": T0 + 3, "kind": "rung_void", "rung": 4, "why": "x"},
            {"ts": T0 + 4, "kind": "reach", "eid": one, "answer": "unchanged", "dt": 80.0, "audit": True},
            {"ts": T0 + 5, "kind": "rung_void", "rung": 6, "issue": 333, "voids_ts": T0 + 1},
            {"ts": T0 + 6, "kind": "reach", "eid": one, "answer": "unchanged", "dt": 15.0},
            {"ts": T0 + 7, "kind": "shift", "eid": one},
            {"ts": T0 + 7, "kind": "reach", "eid": one, "answer": "changed", "dt": 9.0},
            {"ts": T0 + 8, "kind": "reach", "eid": one, "answer": "unchanged", "dt": 25.0}], 2),
        "random_a": (_world(1, 600), 6),
        "random_b": (_world(2, 900, things=10, summary=True), 10),
        "random_c": (_world(3, 1500, things=15), 15),
        "random_tidy": (_world(4, 800, things=5, messy=False), 5),
    }


def _clean(x):
    return json.loads(json.dumps(x))


def _pins():
    out = {}
    for name, (recs, k) in _cases().items():
        targets = _things(k) + [C + "w:never"]
        tempo = _tempo_for(recs, 7, k)
        kinds = _kinds_for(8, k)
        last = max((float(r["ts"]) for r in recs if r.get("ts") is not None), default=T0)
        o = {"lam": {f"{lk}/{pr}": {e: SL._lam(recs, e, lk, pr) for e in targets} for lk, pr in SETTINGS},
             "lam_of": {e: SL._lam_of(_srv(tempo, 45, 5.0), recs, e) for e in targets},
             "due": []}
        for j, (lk, pr) in enumerate(SETTINGS):
            for dn in (5.0, 900.0):
                for kd in ({}, kinds):
                    srv = _srv(dict(tempo), lk, pr)
                    o["due"].append(list(SL._due(srv, recs, targets[:-1], kd, last + dn, random.Random(f"d{j}{dn}"))))
        out[name] = _clean(o)
    return out


PINNED_PATH = Path(__file__).with_name("data") / "due_one_pass_346.json"


def _same(a, b):
    """Equal; a float may differ in its last bits across platforms' math libraries."""
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-9,
                                                                                          abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_due_and_the_rate_fit_give_the_old_codes_numbers():
    pinned = json.loads(PINNED_PATH.read_text())
    got = _pins()
    assert set(got) == set(pinned)
    for name in pinned:
        for k in pinned[name]:
            assert _same(got[name][k], pinned[name][k]), (name, k)


@pytest.mark.parametrize("seed", range(40))
def test_the_new_due_and_lam_equal_the_old_on_random_records(seed):
    rng = random.Random(2000 + seed)
    k = rng.randrange(1, 20)
    recs = _world(seed + 300, rng.randrange(0, 900), things=k, summary=rng.random() < 0.3,
                  messy=rng.random() < 0.7)
    targets = _things(k) + ([C + "w:never"] if rng.random() < 0.5 else [])
    rng.shuffle(targets)
    tempo = _tempo_for(recs, seed, k)
    kinds = _kinds_for(seed, k) if rng.random() < 0.6 else {}
    lk, pr = SETTINGS[seed % len(SETTINGS)]
    for e in targets:
        assert _same(SL._lam(recs, e, lk, pr), _old_lam(recs, e, lk, pr)), e
    last = max((float(r["ts"]) for r in recs if r.get("ts") is not None), default=T0)
    for dn in (1.0, 30.0, 3600.0):
        a = SL._due(_srv(dict(tempo), lk, pr), recs, targets, kinds, last + dn, random.Random(seed))
        b = _old_due(_srv(dict(tempo), lk, pr), recs, targets, kinds, last + dn, random.Random(seed))
        assert _same(list(a), list(b)), (dn, a, b)


class _Record(list):
    """A record that counts the passes made over it."""

    def __init__(self, *a):
        super().__init__(*a)
        self.passes = 0

    def __iter__(self):
        self.passes += 1
        return list.__iter__(self)

    def __reversed__(self):
        self.passes += 1
        return list.__reversed__(self)


def _passes(k):
    recs = _Record(_world(99, 1500, things=k))
    tempo = _tempo_for(recs, 5, k)
    for t in tempo.values():
        t.pop("last", None)                        # every target due: each one's rate is read
    srv = _srv(tempo)
    recs.passes = 0
    SL._due(srv, recs, _things(k), {}, T0 + 1e7, random.Random(3))
    return recs.passes


def test_due_reads_the_record_a_fixed_number_of_times_whatever_the_targets():
    few, many = _passes(2), _passes(30)
    assert many - few <= 4, (few, many)            # was ~2 passes per target: one to fit, one for its shift


def test_nothing_is_kept_across_calls():
    recs = _world(5, 300, things=3)
    srv = _srv({})
    e = C + "w:t0"
    a = SL._lam_of(srv, recs, e)
    SL._due(srv, recs, _things(3), {}, T0 + 1e7, random.Random(1))
    recs.append({"ts": T0 + 1e7, "kind": "shift", "eid": e})
    recs.append({"ts": T0 + 1e7 + 1, "kind": "reach", "eid": e, "answer": "changed", "dt": 1.0})
    assert SL._lam_of(srv, recs, e) == _old_lam_of(srv, recs, e)
    assert SL._SCOPE.get() is None and a == _old_lam_of(srv, recs[:-2], e)


def test_an_odd_eid_reads_as_before():
    e, odd = C + "w:t0", [C + "w:t0"]
    recs = _world(6, 200, things=1)
    recs[5:5] = [{"ts": T0 + 40, "kind": "reach", "eid": odd, "answer": "changed", "dt": 5.0},
                 {"ts": T0 + 41, "kind": "shift", "eid": odd},
                 {"ts": T0 + 42, "kind": "reach", "eid": odd, "answer": "unchanged", "dt": 50.0}]
    for x in (e, odd):
        assert _same(SL._lam(recs, x), _old_lam(recs, x)), x
