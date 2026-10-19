"""Run the petri-dish experiment over a COPY of self's record (#161 lab).

    python experiments/petri_dish/run.py [record.copy.jsonl] [results.json]

Reads only the copy; writes only results.json (aggregate numbers, eid names).
Every method sees each thing's answered looks in time order (predict, then
learn); the first WARM share of each thing's looks is scored by nobody.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dish as D  # noqa: E402

WARM = 0.30            # each thing's first 30% of looks: warm-up, unscored for every method
FEW = 20               # a thing with fewer answered looks is flagged
ETAS = (0.5, 1, 2, 4, 8, 16, 32)
ETA_DEFAULT = 2.0      # pre-registered Hedge rate (the rest of ETAS is a hindsight sweep)
GROWS = (0.1, 0.25, 0.5, 1.0)   # dish sweep; D.GROW (0.25) is the pre-registered one
SEED = 161
BOOT = 2000
LAM_LOOKS = 30         # self_loop.LAM_LOOKS


# ---- the data ---------------------------------------------------------------------

def load(path):
    return [json.loads(line) for line in open(path) if line.strip()]


def kind_of(eid, rate):
    if ":chat:in:" in eid:
        return "chat:in"
    if ":toy:" in eid:
        return "toys"
    return "moving" if rate >= 0.6 else ("still" if rate <= 0.2 else "intermittent")


def sequences(recs):
    """eid -> [(ts, dt, y, p_A, p_B, nfit)] over its ANSWERED looks outside
    audits. dt is the recorded dt (time since self's previous answered look at
    it, what self itself used), else the gap to the previous included look.
    p_B is the faithful offline re-implementation of self's predictor; nfit the
    looks its rate was fitted over (0: it used the one bit instead)."""
    tempo, shift_ts, lam_obs = {}, {}, {}
    seen_timed = False
    seqs = {}
    for r in recs:
        k = r.get("kind")
        if k == "shift":                                    # self's rung-5 re-learn (as recorded)
            t = tempo.setdefault(r["eid"], {"changed": 0, "answered": 0})
            w = int(r["looks"])
            t.update({"changed": int(round(float(r["now"]) * w)), "answered": w})
            t.pop("after", None)
            shift_ts[r["eid"]] = r["ts"]
            continue
        if k != "reach":
            continue
        eid, ans = r["eid"], r["answer"]
        seen_timed = seen_timed or bool(r.get("timed"))
        t = tempo.setdefault(eid, {"changed": 0, "answered": 0})
        # -- p_B: what self_loop._expect would say before this look
        use_lam = r.get("timed") or (r.get("reply") and seen_timed)
        lam, nfit = None, 0
        if use_lam:
            since = shift_ts.get(eid)
            obs = [(d, y) for (ts, d, y) in lam_obs.get(eid, []) if since is None or ts > since][-LAM_LOOKS:]
            nfit = len(obs)
            if obs:
                dt = np.array([o[0] for o in obs])[None, :]
                ch = np.array([o[1] for o in obs], dtype=float)[None, :]
                p = np.clip(1.0 - np.exp(-D.LAM_GRID[:, None] * dt), 1e-9, 1 - 1e-9)
                ll = (ch * np.log(p) + (1 - ch) * np.log(1 - p)).sum(axis=1)
                lam = float(D.LAM_GRID[int(np.argmax(ll))])
        dtr = r.get("dt")
        fitted = nfit if (lam and dtr is not None) else 0
        if lam and dtr is not None:
            pB = 1.0 - math.exp(-lam * float(dtr))
        else:
            aft, prev = t.get("after") or {}, t.get("prev")
            if prev in aft and aft[prev][1] > 0:
                pB = (aft[prev][0] + 0.5) / (aft[prev][1] + 1.0)
            else:
                pB = (t["changed"] + 0.5) / (t["answered"] + 1.0)
        # -- learn (self_loop._notice: a rung-2 audit learns nothing; audit5 does)
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
            if not r.get("audit") and not r.get("audit5"):
                if dtr:
                    lam_obs.setdefault(eid, []).append((r["ts"], float(dtr), y))
                s = seqs.setdefault(eid, [])
                dt = float(dtr) if dtr is not None else ((r["ts"] - s[-1][0]) if s else None)
                s.append((r["ts"], dt, y, float(r["p"]), pB, fitted))
    return seqs


# ---- the methods ----------------------------------------------------------------------

def methods():
    V = D.make_variants
    m = {"static_mix": lambda: D.StaticMix(V())}
    for eta in ETAS:
        m[f"hedge_eta{eta:g}"] = (lambda e: lambda: D.Hedge(V(), eta=e))(eta)
    for g in GROWS:
        for mode in ("mix", "sample", "fire"):
            m[f"dish_{mode}_g{g:g}"] = (lambda g, mode: lambda: D.Dish(V(), grow=g, mode=mode, seed=SEED))(g, mode)
    for i, v in enumerate(V()):
        m[f"single:{v.name}"] = (lambda i: lambda: D.Single(V()[i]))(i)
    return m


def brier(p, y):
    return (np.asarray(p) - np.asarray(y)) ** 2


def calib(ps, ys, bins=10):
    ps, ys = np.asarray(ps), np.asarray(ys)
    out, ece = [], 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = (ps >= lo) & ((ps < hi) if b < bins - 1 else (ps <= hi))
        if sel.sum():
            mp, oy = float(ps[sel].mean()), float(ys[sel].mean())
            out.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": int(sel.sum()), "mean_p": round(mp, 3), "observed": round(oy, 3)})
            ece += sel.sum() / len(ps) * abs(mp - oy)
    return {"ece": round(float(ece), 4), "bins": out}


def main(path=HERE / "record.copy.jsonl", out=HERE / "results.json"):
    recs = load(path)
    seqs = sequences(recs)
    M = methods()
    per = {}                               # eid -> {method: brier array over scored looks}
    pred = {}                              # eid -> {method: p over scored looks}
    info = {}
    for eid, s in sorted(seqs.items()):
        n = len(s)
        w = math.ceil(WARM * n)
        ys = [x[2] for x in s]
        seq = [(x[1], x[2]) for x in s]
        ps = {"self_A": [x[3] for x in s], "self_B": [x[4] for x in s]}
        for name, f in M.items():
            ps[name] = D.run(f, seq)
        rate = sum(ys) / n
        info[eid] = {"n": n, "scored": n - w, "kind": kind_of(eid, rate), "change_rate": round(rate, 3),
                     "few": n < FEW}
        pred[eid] = {k: np.asarray(v[w:]) for k, v in ps.items()}
        pred[eid]["_y"] = np.asarray(ys[w:], dtype=float)
        per[eid] = {k: brier(v[w:], ys[w:]) for k, v in ps.items()}

    names = list(per[next(iter(per))].keys())
    singles = [k for k in names if k.startswith("single:")]
    # hindsight bests: per thing (the best single variant for THAT thing) and global
    for eid in per:
        best = min(singles, key=lambda k: per[eid][k].mean() if len(per[eid][k]) else 0)
        per[eid]["best_single_per_thing(hindsight)"] = per[eid][best]
        info[eid]["best_single"] = best
    pooled_single = {k: np.concatenate([per[e][k] for e in per]).mean() for k in singles}
    gbest = min(pooled_single, key=pooled_single.get)
    for eid in per:
        per[eid]["best_single_global(hindsight)"] = per[eid][gbest]
    names = list(per[next(iter(per))].keys())

    def agg(eids):
        eids = [e for e in eids if len(per[e]["self_A"])]
        res = {}
        for k in names:
            allb = np.concatenate([per[e][k] for e in eids])
            res[k] = {"pooled": round(float(allb.mean()), 4),
                      "macro": round(float(np.mean([per[e][k].mean() for e in eids])), 4)}
        return res, sum(len(per[e]["self_A"]) for e in eids), len(eids)

    overall, nlooks, nth = agg(per)
    kinds = sorted({i["kind"] for i in info.values()})
    by_kind = {}
    for kd in kinds:
        es = [e for e in per if info[e]["kind"] == kd]
        r, nl, nt = agg(es)
        by_kind[kd] = {"things": nt, "scored_looks": nl, "brier": r}

    # paired differences vs the pre-registered dish (mix, GROW=0.25): look-level mean and a
    # thing-block bootstrap 95% CI (resample things with replacement, pooled mean)
    ref = "dish_mix_g0.25"
    rng = random.Random(SEED)
    eids = [e for e in per if len(per[e]["self_A"])]
    paired = {}
    for k in ("self_A", "self_B", "static_mix", f"hedge_eta{ETA_DEFAULT:g}", "dish_sample_g0.25",
              "dish_fire_g0.25", "best_single_global(hindsight)", "best_single_per_thing(hindsight)"):
        d = np.concatenate([per[e][ref] - per[e][k] for e in eids])
        boots = []
        for _ in range(BOOT):
            pick = [eids[rng.randrange(len(eids))] for _ in eids]
            num = sum(float((per[e][ref] - per[e][k]).sum()) for e in pick)
            den = sum(len(per[e][ref]) for e in pick)
            boots.append(num / den)
        lo, hi = np.percentile(boots, [2.5, 97.5])
        paired[f"{ref} - {k}"] = {"mean": round(float(d.mean()), 4), "ci95_thing_bootstrap": [round(float(lo), 4), round(float(hi), 4)]}

    cal = {}
    for k in ("self_A", "self_B", "static_mix", f"hedge_eta{ETA_DEFAULT:g}", ref, "dish_sample_g0.25"):
        cal[k] = calib(np.concatenate([pred[e][k] for e in eids]), np.concatenate([pred[e]["_y"] for e in eids]))
    cal_toys = {}
    tes = [e for e in eids if info[e]["kind"] == "toys"]
    if tes:
        for k in ("self_A", ref):
            cal_toys[k] = {"mean_p": round(float(np.concatenate([pred[e][k] for e in tes]).mean()), 3),
                           "observed": round(float(np.concatenate([pred[e]["_y"] for e in tes]).mean()), 3)}

    # where self's own predictor loses: by how many looks its rate was fitted over
    fitd = {}
    for e in eids:
        w = len(seqs[e]) - len(per[e]["self_A"])
        for x, bA, bR in zip(seqs[e][w:], per[e]["self_A"], per[e]["single:rate_w30"]):
            g = "one bit (no rate)" if x[5] == 0 else ("rate over <10 looks" if x[5] < 10 else "rate over >=10 looks")
            fitd.setdefault(g, []).append((bA, bR))
    fit_diag = {g: {"looks": len(v), "self_A": round(float(np.mean([a for a, _ in v])), 4),
                    "single:rate_w30": round(float(np.mean([b for _, b in v])), 4)} for g, v in sorted(fitd.items())}
    ab = np.concatenate([np.asarray([x[3] for x in seqs[e]]) - np.asarray([x[4] for x in seqs[e]]) for e in seqs])
    res = {
        "constants": {"WARM": WARM, "FEW": FEW, "INIT": D.INIT, "GROW": D.GROW, "GROW_sweep": GROWS,
                      "TIE": D.TIE, "FLOOR": D.FLOOR, "CAP": D.CAP, "ETA_default": ETA_DEFAULT, "ETA_sweep": ETAS,
                      "SEED": SEED, "variants": [v.name for v in D.make_variants()]},
        "data": {"answered_looks": int(sum(len(s) for s in seqs.values())), "things": len(seqs),
                 "scored_looks": nlooks, "scored_things": nth},
        "self_A_vs_B": {"mean_abs_diff": round(float(np.abs(ab).mean()), 4),
                        "share_within_0.01": round(float((np.abs(ab) < 0.01).mean()), 4)},
        "self_loss_by_rate_fit": fit_diag,
        "overall": overall,
        "by_kind": by_kind,
        "paired_vs_dish": paired,
        "calibration": cal,
        "toys_mean_p_vs_observed": cal_toys,
        "best_single_global": gbest,
        "per_thing": {e: {**info[e], "brier": {k: round(float(per[e][k].mean()), 4) if len(per[e][k]) else None
                                               for k in ("self_A", "self_B", "static_mix", f"hedge_eta{ETA_DEFAULT:g}",
                                                         ref, "dish_sample_g0.25", "best_single_per_thing(hindsight)")},
                          "dish_copies_end": dish_copies(seqs[e])}
                      for e in sorted(info)},
    }
    Path(out).write_text(json.dumps(res, indent=1))
    return res


def dish_copies(s):
    d = D.Dish(D.make_variants(), mode="mix", seed=SEED)
    for _, dt, y, *_ in s:
        d.predict(dt)
        d.update(dt, y)
    return {v.name: c for v, c in zip(d.variants, d.copies)}


if __name__ == "__main__":
    a = sys.argv[1:]
    r = main(*(a[:2] or []))
    o = r["overall"]
    keys = ["self_A", "self_B", "static_mix", "hedge_eta2", "dish_mix_g0.25", "dish_sample_g0.25", "dish_fire_g0.25",
            "best_single_global(hindsight)", "best_single_per_thing(hindsight)"]
    print(json.dumps(r["data"]), json.dumps(r["self_A_vs_B"]))
    print(f"{'method':38s} {'pooled':>8s} {'macro':>8s}  " + "  ".join(f"{k[:12]:>12s}" for k in r["by_kind"]))
    for k in sorted(o, key=lambda k: o[k]["pooled"]):
        row = "  ".join(f"{r['by_kind'][kd]['brier'][k]['pooled']:12.4f}" for kd in r["by_kind"])
        print(f"{k:38s} {o[k]['pooled']:8.4f} {o[k]['macro']:8.4f}  {row}")
    print(json.dumps(r["paired_vs_dish"], indent=1))
    print({k: v["ece"] for k, v in r["calibration"].items()})
    print(json.dumps(r["self_loss_by_rate_fit"]))
