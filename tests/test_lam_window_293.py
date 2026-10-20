"""#293: the window sweep's fast rate fit (experiments/lam_window/run.py) is
self_loop._lam itself, made incremental -- equal to it at every look of a
record with several things, audits, shifts and a thing with no looks before
its shift, at each window the sweep reads."""
import importlib.util
import os
import random
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from membrane import self_loop as SL  # noqa: E402


def _run():
    spec = importlib.util.spec_from_file_location("lam_window_run_293", ROOT / "experiments" / "lam_window" / "run.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def _record(seed=7, n=400):
    rng = random.Random(seed)
    eids = ["urn:membrane:Component:a", "urn:membrane:Component:b", "urn:membrane:Component:c"]
    rate = {e: rng.choice([0.002, 0.02, 0.2]) for e in eids}
    recs, ts = [], 1000.0
    for i in range(n):
        ts += rng.uniform(5, 60)
        if i in (120, 260):                                  # the world changes: a recorded shift
            e = rng.choice(eids)
            rate[e] = rng.choice([0.002, 0.02, 0.2])
            recs.append({"ts": ts, "kind": "shift", "eid": e, "was": 0.1, "now": 0.8, "looks": 4})
            continue
        if i == 300:                                         # a thing whose first look comes after its shift
            recs.append({"ts": ts, "kind": "shift", "eid": "urn:membrane:Component:late", "was": 0.1,
                         "now": 0.8, "looks": 3})
            continue
        e = "urn:membrane:Component:late" if i > 300 and i % 7 == 0 else rng.choice(eids)
        dt = rng.uniform(10, 600)
        y = rng.random() < 1 - pow(2.718281828, -rate.get(e, 0.02) * dt)
        r = {"ts": ts, "kind": "reach", "eid": e, "p": 0.5, "dt": dt,
             "answer": rng.choice(["changed", "unchanged", "none"]) if i % 50 == 0 else
             ("changed" if y else "unchanged")}
        if i % 37 == 0:
            r["audit"] = True
        if i % 41 == 0:
            r["audit5"] = True
        recs.append(r)
        if i % 11 == 0:
            recs.append({"ts": ts, "kind": "cycle", "n": i})
    return recs


def test_the_fast_fit_is_self_loops_own_at_every_window():
    R = _run()
    recs = _record()
    eids = sorted({r["eid"] for r in recs if r.get("kind") in ("reach", "shift")})
    for w in R.WINDOWS:
        fast = R.FastLam(w)
        keep = SL.LAM_LOOKS
        try:
            SL.LAM_LOOKS = w
            for i in range(0, len(recs) + 1, 9):
                prefix = recs[:i]
                for e in eids:
                    assert fast(prefix, e) == SL._lam(prefix, e), (w, i, e)
        finally:
            SL.LAM_LOOKS = keep


def test_the_fast_fit_needs_the_prefix_to_only_grow():
    R = _run()
    recs = _record()
    fast = R.FastLam(30)
    fast(recs[:50], "urn:membrane:Component:a")
    try:
        fast(recs[:10], "urn:membrane:Component:a")
    except ValueError:
        return
    raise AssertionError("a shrinking prefix must be refused")


def test_todays_window_is_in_the_sweep():
    R = _run()
    assert SL.LAM_LOOKS in R.WINDOWS
