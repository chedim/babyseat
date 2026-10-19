"""Replay a COPY of self's record with the old and the new rung-4 rate fit (#235).

    python experiments/lam_shrink/run.py [record.copy.jsonl] [--until TS] [--json out.json]

Same harness as the Petri-dish lab (branch exp/petri-dish, experiments/petri_dish/run.py):
each thing's answered looks outside audits, in time order (predict, then learn);
the first 30% of each thing's looks are warm-up, scored by nobody. self's p on a
look is re-computed the way self_loop._expect does it -- 1 - exp(-lam*dt) on a
timed look (and on a reply check once timed looks exist), else the one bit --
with `lam` from:
  * old: the rate fit before #235 (the MLE over the looks since the last shift
    only), copied here verbatim;
  * new: membrane.self_loop._lam as it is in this tree (the shrunk fit);
  * new_kK: the same with LAM_PRIOR_LOOKS = K (a hindsight sweep).
Reads only the copy; prints the table (and writes aggregate numbers with --json).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from membrane import self_loop as SL  # noqa: E402

WARM = 0.30
SHORT = 10            # a fit over fewer looks than this is "short"
SWEEP = (1.0, 2.0, 3.0, 5.0, 10.0)


def lam_old(recs, eid):
    """self_loop._lam before #235, verbatim."""
    since = SL._last_shift_ts(recs, eid)
    obs = [(float(r["dt"]), r["answer"] == "changed") for r in recs
           if r.get("kind") == "reach" and r.get("eid") == eid
           and not r.get("audit") and not r.get("audit5")
           and r.get("answer") in ("changed", "unchanged") and r.get("dt")
           and (since is None or r["ts"] > since)][-SL.LAM_LOOKS:]
    if not obs:
        return None
    dt = np.array([o[0] for o in obs])[None, :]
    ch = np.array([o[1] for o in obs], dtype=float)[None, :]
    lam = SL.LAM_GRID[:, None]
    p = np.clip(1.0 - np.exp(-lam * dt), 1e-9, 1 - 1e-9)
    ll = (ch * np.log(p) + (1 - ch) * np.log(1 - p)).sum(axis=1)
    return float(SL.LAM_GRID[int(np.argmax(ll))])


def lam_new(k=None):
    def f(recs, eid):
        if k is None:
            return SL._lam(recs, eid)
        keep = SL.LAM_PRIOR_LOOKS
        SL.LAM_PRIOR_LOOKS = k
        try:
            return SL._lam(recs, eid)
        finally:
            SL.LAM_PRIOR_LOOKS = keep
    return f


def replay(recs, fits):
    """eid -> [(y, p_A, {fit: p}, {fit: lam}, nfit)] over answered looks outside audits.
    nfit: the looks since the last shift the rate is fitted over (0: the one bit)."""
    tempo, prefix, seqs = {}, [], {}
    seen_timed = False
    for r in recs:
        k = r.get("kind")
        if k == "shift":
            t = tempo.setdefault(r["eid"], {"changed": 0, "answered": 0})
            w = int(r["looks"])
            t.update({"changed": int(round(float(r["now"]) * w)), "answered": w})
            t.pop("after", None)
            prefix.append(r)
            continue
        if k != "reach":
            continue
        eid, ans = r["eid"], r["answer"]
        seen_timed = seen_timed or bool(r.get("timed"))
        t = tempo.setdefault(eid, {"changed": 0, "answered": 0})
        dtr = r.get("dt")
        use_lam = (r.get("timed") or (r.get("reply") and seen_timed)) and dtr is not None
        ps, lams, nfit = {}, {}, 0
        aft, prev = t.get("after") or {}, t.get("prev")
        onebit = ((aft[prev][0] + 0.5) / (aft[prev][1] + 1.0) if prev in aft and aft[prev][1] > 0
                  else (t["changed"] + 0.5) / (t["answered"] + 1.0))
        scored = ans in ("changed", "unchanged") and not r.get("audit") and not r.get("audit5")
        if scored:
            for name, f in fits.items():
                lam = f(prefix, eid) if use_lam else None
                lams[name] = lam
                ps[name] = (1.0 - math.exp(-lam * float(dtr))) if lam else onebit
            if use_lam and lams.get("old"):
                since = SL._last_shift_ts(prefix, eid)
                nfit = min(SL.LAM_LOOKS, sum(1 for q in prefix if q.get("kind") == "reach" and q["eid"] == eid
                                             and not q.get("audit") and not q.get("audit5") and q.get("dt")
                                             and q.get("answer") in ("changed", "unchanged")
                                             and (since is None or q["ts"] > since)))
        if ans in ("changed", "unchanged"):
            y = int(ans == "changed")
            if not r.get("audit"):
                prev = t.get("prev")
                t["answered"] += 1
                t["changed"] += y
                if prev in ("changed", "unchanged"):
                    e = t.setdefault("after", {}).setdefault(prev, [0, 0])
                    e[0] += y
                    e[1] += 1
                t["prev"] = ans
            if scored:
                seqs.setdefault(eid, []).append((y, float(r["p"]), ps, lams, nfit,
                                                 bool(SL._last_shift_ts(prefix, eid) is not None)))
        prefix.append(r)
    return seqs


def ece(ps, ys, bins=10):
    ps, ys = np.asarray(ps), np.asarray(ys)
    out = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = (ps >= lo) & ((ps < hi) if b < bins - 1 else (ps <= hi))
        if sel.sum():
            out += sel.sum() / len(ps) * abs(ps[sel].mean() - ys[sel].mean())
    return float(out)


def main(path, until=None):
    recs = [json.loads(x) for x in open(path) if x.strip()]
    if until is not None:
        recs = [r for r in recs if float(r.get("ts", 0)) <= until]
    fits = {"old": lam_old, "new": lam_new()}
    fits.update({f"new_k{k:g}": lam_new(k) for k in SWEEP})
    seqs = replay(recs, fits)
    rows = []                                      # scored looks only
    for eid, s in sorted(seqs.items()):
        w = math.ceil(WARM * len(s))
        rows += s[w:]
    ys = np.array([x[0] for x in rows], dtype=float)
    short = np.array([0 < x[4] < SHORT for x in rows])
    shifted = np.array([0 < x[4] < SHORT and x[5] for x in rows])     # ...and since a shift
    long_ = np.array([x[4] >= SHORT for x in rows])
    edges = (float(SL.LAM_GRID[0]), float(SL.LAM_GRID[-1]))
    res = {"looks": len(rows), "short_fit_looks": int(short.sum()), "short_after_shift": int(shifted.sum()), "long_fit_looks": int(long_.sum()),
           "things": len(seqs), "methods": {}}
    cols = {"self_A (recorded p)": np.array([x[1] for x in rows])}
    for name in fits:
        cols[name] = np.array([x[2][name] for x in rows])
    for name, p in cols.items():
        b = (p - ys) ** 2
        m = {"brier": round(float(b.mean()), 4),
             "brier_short": round(float(b[short].mean()), 4),
             "brier_shift": round(float(b[shifted].mean()), 4),
             "brier_long": round(float(b[long_].mean()), 4),
             "ece": round(ece(p, ys), 4),
             "short_p_extreme": int(((p < 0.05) | (p > 0.95))[short].sum()),
             "short_p_extreme_wrong": int((((p < 0.05) & (ys == 1)) | ((p > 0.95) & (ys == 0)))[short].sum())}
        if name in fits:
            for key, sel in (("short_lam_at_edge", short), ("shift_lam_at_edge", shifted)):
                lams = [x[3].get(name) for x, s in zip(rows, sel) if s]
                m[key] = sum(1 for v in lams if v is not None and v in edges)
        res["methods"][name] = m
    return res


if __name__ == "__main__":
    a = sys.argv[1:]
    until = float(a[a.index("--until") + 1]) if "--until" in a else None
    out = a[a.index("--json") + 1] if "--json" in a else None
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    r = main(pos[0] if pos else HERE / "record.copy.jsonl", until)
    print(f"scored looks {r['looks']} over {r['things']} things; short-fit (<{SHORT}) {r['short_fit_looks']} "
          f"(after a shift {r['short_after_shift']}), >= {SHORT}: {r['long_fit_looks']}")
    print(f"{'method':22s} {'brier':>7s} {'short':>7s} {'shift':>7s} {'>=10':>7s} {'ece':>7s} "
          f"{'p~0/1':>6s} {'wrong':>6s} {'edge':>5s} {'edge@shift':>10s}")
    for k, m in r["methods"].items():
        print(f"{k:22s} {m['brier']:7.4f} {m['brier_short']:7.4f} {m['brier_shift']:7.4f} {m['brier_long']:7.4f} "
              f"{m['ece']:7.4f} {m['short_p_extreme']:6d} {m['short_p_extreme_wrong']:6d} "
              f"{m.get('short_lam_at_edge', '-'):>5} {m.get('shift_lam_at_edge', '-'):>10}")
    if out:
        Path(out).write_text(json.dumps(r, indent=1))
