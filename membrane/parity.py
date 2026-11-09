"""#980 Q3: the reusable before/after check -- boot time, hot-path time, field-state parity.

snapshot(boot, hot) -> {"boot_ms", "hot_us", "state"}; diff(before, after) -> the parity diff.
CLI: python -m membrane.parity snapshot out.json | compare before.json after.json [--max-slow 1.5]
"""
from __future__ import annotations

import json
import sys
import time


def _state(est) -> dict:
    return {eid: {"cls": d.cls, "properties": json.loads(json.dumps(d.properties, default=str, sort_keys=True))}
            for eid, d in sorted((est.docs or {}).items())}


def snapshot(boot, hot, runs: int = 200) -> dict:
    """boot() -> a fresh booted store (timed once); hot(est) is the hot path (timed `runs` times)."""
    t = time.perf_counter()
    est = boot()
    boot_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    for _ in range(runs):
        hot(est)
    return {"boot_ms": round(boot_ms, 3), "hot_us": round((time.perf_counter() - t) / runs * 1e6, 3),
            "state": _state(est)}


def diff(before: dict, after: dict) -> dict:
    b, a = before["state"], after["state"]
    changed = {e: {"before": b[e], "after": a[e]} for e in b.keys() & a.keys() if b[e] != a[e]}
    return {"added": sorted(a.keys() - b.keys()), "removed": sorted(b.keys() - a.keys()), "changed": changed}


def verdict(before: dict, after: dict, max_slow: float = 1.5, allow_added: bool = True) -> dict:
    d = diff(before, after)
    bad = []
    if d["removed"] or d["changed"] or (d["added"] and not allow_added):
        bad.append("parity")
    for k in ("boot_ms", "hot_us"):
        if before[k] > 0 and after[k] / before[k] > max_slow:
            bad.append(k)
    return {"ok": not bad, "bad": bad, "diff": d, "boot_ms": [before["boot_ms"], after["boot_ms"]],
            "hot_us": [before["hot_us"], after["hot_us"]]}


def _banner_run():
    from . import graphbanner as GB
    from .entities import EntityStore

    def boot():
        est = EntityStore(path=None, shared=False)
        GB.seed(est)
        return est
    return boot, GB._rules


def main(argv) -> int:
    import os
    os.environ.setdefault("MEMBRANE_STORE", "json")
    os.environ.setdefault("MEMBRANE_RUN", "0")
    if len(argv) >= 2 and argv[0] == "snapshot":
        boot, hot = _banner_run()
        json.dump(snapshot(boot, hot), open(argv[1], "w"), indent=1, sort_keys=True)
        return 0
    if len(argv) >= 3 and argv[0] == "compare":
        slow = float(argv[argv.index("--max-slow") + 1]) if "--max-slow" in argv else 1.5
        v = verdict(json.load(open(argv[1])), json.load(open(argv[2])), slow)
        print(json.dumps({k: v[k] for k in ("ok", "bad", "boot_ms", "hot_us")}), json.dumps(v["diff"]))
        return 0 if v["ok"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
