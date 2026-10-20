"""Replay a COPY of self's record through change itself's dream, hour by hour (#320).

    python experiments/self_change/run.py [record.copy.jsonl] [--since TS]

What would Blurp have dreamt, adopted and checked had change itself been open
since --since (default: patience's first timed look)? Every CHANGE_EVERY cycle
lines, self_loop._dream runs on the record as it stood then (plus the change
lines this replay wrote), exactly as the live loop would hand it a snapshot;
an adoption changes the settings the next dreams start from. The test is read
after every dream. Also: the cost of one dream on the whole copy (the live
seat's size), and the dream's fit checked against _lam on a sample of looks.
Reads only the copy; prints the dreams and the summary.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from membrane import self_loop as SL  # noqa: E402


def check_fit(recs: list[dict], n: int = 300, seed: int = 0) -> int:
    """The dream's fit (_RateFit) against _lam at n sampled looks and every
    setting pair at the grid's ends and today's: -> mismatches (0)."""
    rng = random.Random(seed)
    idx = SL._rate_looks(recs)
    pick = set(rng.sample(idx, min(n, len(idx))))
    vs = [(w, k) for w in (10, SL.LAM_LOOKS, 120) for k in (1.0, SL.LAM_PRIOR_LOOKS, 10.0)]
    got = SL._replay_at(recs, {v: pick for v in vs})
    bad = 0
    for i in sorted(pick)[:: max(1, len(pick) // 60)]:          # _lam is O(record): a subsample
        for v in vs:
            lam = SL._lam(recs[:i], recs[i]["eid"], looks=v[0], prior=v[1])
            p = None if not lam else 1.0 - __import__("math").exp(-lam * float(recs[i]["dt"]))
            bad += (p is None) != (got[v][i] is None) or (p is not None and abs(p - got[v][i]) > 1e-12)
    return bad


def main(path, since=None):
    recs = [json.loads(x) for x in open(path) if x.strip()]
    t = time.time()
    SL._dream(recs, SL._defaults(), recs[-1]["ts"] + 1, 0)
    cost = time.time() - t
    first = next(r["ts"] for r in recs if r.get("kind") == "reach" and r.get("timed"))
    since = first if since is None else since
    settings, lines, cycles, out, costs = SL._defaults(), [], 0, [], []
    passed = None
    for j, r in enumerate(recs):
        if r.get("kind") != "cycle":
            continue
        cycles += 1
        if cycles % SL.CHANGE_EVERY or r["ts"] < since:
            continue
        snap = recs[:j + 1]
        k = sum(1 for x in lines if x["kind"] == "change")
        t = time.time()
        got = SL._dream(lines + snap, settings, r["ts"], k)
        costs.append((time.time() - t, len(snap), sum(1 for x in got if x["kind"] == "change_check")))
        for line in got:
            if line["kind"] == "change" and line["verdict"] == "adopted":
                settings = {**settings, line["setting"]: line["to"]}
            lines.append(line)
            out.append(line)
        p = SL._rung10(lines)
        if p["passed"] and passed is None:
            passed = r["ts"]
    return {"lines": out, "settings": settings, "test": SL._rung10(lines), "passed": passed,
            "cost_s": round(cost, 3), "record_lines": len(recs), "fit_mismatches": check_fit(recs),
            "dream_s": {"median": round(sorted(c[0] for c in costs)[len(costs) // 2], 3) if costs else None,
                        "max": round(max(c[0] for c in costs), 3) if costs else None,
                        "max_at_lines": max(costs)[1] if costs else None, "max_checks": max(costs)[2] if costs else None}}


if __name__ == "__main__":
    a = sys.argv[1:]
    since = float(a[a.index("--since") + 1]) if "--since" in a else None
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    r = main(pos[0] if pos else HERE / "record.copy.jsonl", since)
    for x in r["lines"]:
        if x["kind"] == "change":
            print(f"dream  {x['setting']:16s} {x['from']!s:>5} -> {x['to']!s:<5} gain {x['gain']:+.4f} z {x['z']:5.1f}  "
                  f"{x['verdict']}")
        else:
            print(f"  check {x['setting']:15s} {x['from']!s:>5} -> {x['to']!s:<5} gain {x['gain']:+.4f} z {x['z']:5.1f}  "
                  f"(was {x['verdict']}{', held' if x.get('held') else ''}{', MISS' if x.get('miss') else ''})")
    t = r["test"]
    print(f"\nrecord lines {r['record_lines']}, one dream on the whole copy: {r['cost_s']} s; "
          f"fit vs _lam mismatches: {r['fit_mismatches']}; each replayed dream (s): {r['dream_s']}")
    print(f"settings at the end: {r['settings']}; passed: {r['passed']}")
    print(f"test: {t['crit']} adopted {t['adopted']} held {t['held']} misses {t['misses']} "
          f"gain_after {t['gain_after']} z_after {t['z_after']}")
