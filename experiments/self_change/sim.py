"""Chance and power of change itself's test (#320), simulated at the live seat's pace.

    python experiments/self_change/sim.py truth                 # each world's true gains per neighbour
    python experiments/self_change/sim.py run [--seeds 100] [--days 7] [--looks 1000 --z 3 --margin 0.001]

A WORLD is a stream of patience's timed looks (~335 an hour, the live seat's
rate): things that change at their own rate, some switching between two rates
in sessions (a rung-5 `shift` line follows most switches a few looks later,
and now and then one comes with no switch), each look's dt drawn around the
thing's own tempo. Every look carries `q`, the TRUE chance it changed, so the
true expected Brier gain of one setting over another is known exactly:
E[(p - y)^2] = (p - q)^2 + q(1 - q).

A BLURP dreams once an hour with self_loop's own _dream (the replay, the
candidate, the scores, the checks), and its policy decides what it adopts:
  honest -- self_loop's rule (gain >= CHANGE_MARGIN at z >= CHANGE_Z);
  never  -- dreams and scores, adopts nothing;
  random -- adopts the candidate on a coin flip, whatever the replay says;
  greedy -- adopts any positive gain (no significance).
Its test (_rung10) is read after every dream, like the live loop reads it.
Reported per world x policy over the seeds: the share that passes within the
run (and the share that would pass on dreams + judgement alone, without the
honesty stage), the median hours to pass, adoptions per run and the share of
them that were truly better on the CHANGE_LOOKS looks after them.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import random
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from membrane import self_loop as SL  # noqa: E402

PER_HOUR = 335          # the live seat's rate-read looks an hour (2026-10-16..20)
LOOK_S = 3600.0 / PER_HOUR
CHECK_ZS = (2.0, 2.5, 3.0)   # the check bar only reads the lines: each run is scored at each of these
TAIL = 6000             # the looks a dream is handed (all a 120-look window, a check and a proposal can read)

# name -> (things, (lo, hi) rate per s, session dwell in a thing's own looks (None: steady), rate factor,
#          the settings Blurp starts from)
WORLDS = {
    "steady": (15, (0.0003, 0.02), None, 1.0, {}),
    "sessions": (15, (0.0003, 0.02), 60, 20.0, {}),
    "fast": (15, (0.0003, 0.02), 6, 20.0, {}),
    # THE NULL: the steady world from a window of 90, where no neighbour is worth a change -- each
    # one's true gain is under CHANGE_MARGIN (`truth --flat`: 120 +0.0003, 60 -0.0006, prior 5
    # +0.0006, prior 2 -0.0004). Any pass here is chance.
    "flat": (15, (0.0003, 0.02), None, 1.0, {"lam_looks": 90}),
}


class World:
    def __init__(self, name: str, seed: int):
        k, (lo, hi), self.dwell, self.f, _ = WORLDS[name]
        self.rng = random.Random(seed)
        self.eids = [f"membrane::Component:w{j}" for j in range(k)]
        self.base = {e: math.exp(self.rng.uniform(math.log(lo), math.log(hi))) for e in self.eids}
        self.hot = {e: False for e in self.eids}
        self.due: dict = {}                       # eid -> looks until its shift line
        self.ts = 1_000_000.0

    def rate(self, e):
        return self.base[e] * (self.f if self.hot[e] else 1.0)

    def looks(self, n: int) -> list[dict]:
        out, rng = [], self.rng
        for _ in range(n):
            e = rng.choice(self.eids)
            if self.dwell and rng.random() < 1.0 / self.dwell:
                self.hot[e] = not self.hot[e]
                if rng.random() < 0.7:
                    self.due[e] = rng.randint(3, 5)
            r = self.rate(e)
            dt = min(1800.0, max(10.0, rng.uniform(0.3, 3.0) / r))
            q = 1.0 - math.exp(-r * dt)
            self.ts += LOOK_S
            out.append({"ts": round(self.ts, 3), "kind": "reach", "eid": e, "p": 0.5, "dt": round(dt, 3),
                        "timed": True, "answer": "changed" if rng.random() < q else "unchanged", "q": round(q, 6)})
            if e in self.due:
                self.due[e] -= 1
                if self.due[e] <= 0:
                    del self.due[e]
                    out.append({"ts": round(self.ts, 3), "kind": "shift", "eid": e, "was": 0.1, "now": 0.8,
                                "looks": 4})
            elif rng.random() < 1 / 300:                    # a shift noticed with no switch behind it
                out.append({"ts": round(self.ts, 3), "kind": "shift", "eid": e, "was": 0.1, "now": 0.8, "looks": 4})
        return out


def true_gain(recs: list[dict], a: tuple, b: tuple, idx: list[int] | None = None) -> float:
    """The true expected Brier gain per look of b over a on the rate-read looks (idx: which)."""
    idx = SL._rate_looks(recs) if idx is None else idx
    got = SL._replay_at(recs, {a: idx, b: idx})
    g = [(got[a][i] - recs[i]["q"]) ** 2 - (got[b][i] - recs[i]["q"]) ** 2 for i in idx
         if got[a][i] is not None and got[b][i] is not None]
    return float(np.mean(g)) if g else 0.0


def truth(seeds=5, n=40_000, worlds=None):
    """Each world's true gain per look of every setting against where it starts."""
    out = {}
    for w in worlds or WORLDS:
        st = {**SL._defaults(), **WORLDS[w][4]}
        base = (st["lam_looks"], st["lam_prior_looks"])
        rows = {}
        for s in range(seeds):
            recs = World(w, 1000 + s).looks(n)
            for v in [(x, base[1]) for x in SL.CHANGE_SETTINGS["lam_looks"]] + \
                     [(base[0], x) for x in SL.CHANGE_SETTINGS["lam_prior_looks"]]:
                rows.setdefault(v, []).append(true_gain(recs, base, v))
        out[w] = {f"{v[0]}/{v[1]:g}": round(float(np.mean(g)), 5) for v, g in rows.items()}
    return out


def run_one(args) -> dict:
    world, policy, seed, days, bars = args
    for k, v in bars.items():
        setattr(SL, k, v)
    W = World(world, seed)
    rng = random.Random(seed * 7919 + 13)
    looks: list[dict] = []
    lines: list[dict] = []                         # change / change_check lines
    settings = {**SL._defaults(), **WORLDS[world][4]}
    passed_h = judged_h = None
    by_cz: dict = {}
    adoptions = []
    W.looks(0)
    warm = W.looks(3 * SL.CHANGE_LOOKS)            # a seat with history before change opens
    looks += warm
    for hour in range(1, 24 * days + 1):
        looks += W.looks(PER_HOUR)
        tail = looks[-TAIL:]
        now = tail[-1]["ts"] + 1.0
        out = SL._dream(lines + tail, settings, now, len([r for r in lines if r["kind"] == "change"]))
        for line in out:
            if line["kind"] == "change":
                gain_ok = line["gain"] > 0
                adopt = {"honest": line["verdict"] == "adopted", "never": False,
                         "random": rng.random() < 0.5, "greedy": gain_ok}[policy]
                if adopt != (line["verdict"] == "adopted"):
                    line = {**line, "verdict": "adopted" if adopt else "rejected", "reason": f"policy {policy}"}
                if adopt:
                    settings = {**settings, line["setting"]: line["to"]}
                    adoptions.append({"ts": now, "from": SL._variant(line["base"]), "to": SL._variant(settings)})
            lines.append(line)
        keep = SL.CHANGE_CHECK_Z
        for cz in CHECK_ZS:
            SL.CHANGE_CHECK_Z = cz
            if SL._rung10(lines)["passed"] and cz not in by_cz:
                by_cz[cz] = hour
        SL.CHANGE_CHECK_Z = keep
        p = SL._rung10(lines)
        if p["passed"] and passed_h is None:
            passed_h = hour
        if p["crit"]["dreams"] >= 1 and p["crit"]["judgement"] >= 1 and judged_h is None:
            judged_h = hour
    good = 0                                       # adoptions truly better on the looks after them
    for a in adoptions:
        after = [r for r in looks if r["ts"] > a["ts"]]
        if len(after) < SL.CHANGE_LOOKS:
            continue
        seg = looks[max(0, looks.index(after[0]) - TAIL // 2):]
        idx = [i for i in SL._rate_looks(seg) if seg[i]["ts"] > a["ts"]][:SL.CHANGE_LOOKS]
        good += true_gain(seg, a["from"], a["to"], idx) > 0
    return {"world": world, "policy": policy, "seed": seed, "passed_h": passed_h, "judged_h": judged_h,
            "passed_h_by_check_z": {str(k): v for k, v in by_cz.items()},
            "adoptions": len(adoptions), "good": good,
            "settings": settings, "dreams": sum(1 for r in lines if r["kind"] == "change")}


def summarize(res: list[dict]) -> dict:
    out = {}
    for r in res:
        out.setdefault((r["world"], r["policy"]), []).append(r)
    rows = {}
    for (w, p), rs in sorted(out.items()):
        hs = sorted(r["passed_h"] for r in rs if r["passed_h"] is not None)
        n_ad = sum(r["adoptions"] for r in rs)
        rows[f"{w}/{p}"] = {"runs": len(rs), "pass": round(len(hs) / len(rs), 3),
                            "pass_no_honesty": round(sum(r["judged_h"] is not None for r in rs) / len(rs), 3),
                            "median_h": hs[len(hs) // 2] if hs else None,
                            "pass_by_check_z": {str(cz): round(sum(str(cz) in r["passed_h_by_check_z"] for r in rs) / len(rs), 3)
                                                for cz in CHECK_ZS},
                            "adoptions_per_run": round(n_ad / len(rs), 2),
                            "truly_better": round(sum(r["good"] for r in rs) / n_ad, 3) if n_ad else None}
    return rows


def main(argv):
    if argv[:1] == ["truth"]:
        print(json.dumps(truth(worlds=[w for w in WORLDS if f"--{w}" in argv] or None), indent=1))
        return
    a = argv[1:]

    def opt(k, d, f=float):
        return f(a[a.index(k) + 1]) if k in a else d
    seeds, days = opt("--seeds", 100, int), opt("--days", 7, int)
    bars = {"CHANGE_LOOKS": opt("--looks", SL.CHANGE_LOOKS, int), "CHANGE_Z": opt("--z", SL.CHANGE_Z),
            "CHANGE_MARGIN": opt("--margin", SL.CHANGE_MARGIN), "CHANGE_CHECK_Z": opt("--check-z", SL.CHANGE_CHECK_Z)}
    worlds = [w for w in WORLDS if f"--{w}" in a] or list(WORLDS)
    pols = [p for p in ("honest", "never", "random", "greedy") if f"--{p}" in a] or ["honest", "never", "random", "greedy"]
    jobs = [(w, p, s, days, bars) for w in worlds for p in pols for s in range(seeds)]
    t = time.time()
    with mp.Pool(opt("--procs", 6, int)) as pool:
        res = pool.map(run_one, jobs, chunksize=1)
    rows = summarize(res)
    print(json.dumps({"bars": bars, "seeds": seeds, "days": days, "s": round(time.time() - t), "rows": rows}, indent=1))
    if "--out" in a:
        Path(a[a.index("--out") + 1]).write_text(json.dumps({"bars": bars, "res": res, "rows": rows}))


if __name__ == "__main__":
    main(sys.argv[1:])
