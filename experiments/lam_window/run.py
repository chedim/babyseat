"""Replay a COPY of self's record with rung 4's rate fit over different windows (#293).

    python experiments/lam_window/run.py [record.copy.jsonl] [--since TS]

--since scores only the looks after TS (the replay still learns from all of
them): self's recorded p came from a different fit before #235, so only looks
after #235 reached the seat can check the replay against what self predicted.

LAM_LOOKS (how many of a thing's answered looks its rate of change is fitted
over) was 30 by hand since #178. This sweeps it through the lam_shrink harness
(experiments/lam_shrink/run.py: the same warm-up, the timed / one-bit logic and
the RECORDED shifts), so the window self runs today reproduces self's recorded
p. The fit is self_loop._lam itself, made incremental (FastLam: equal to it at
every look, tests/test_lam_window_293.py) so a whole record replays in minutes.
Reads only the copy; prints the tables.
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from membrane import self_loop as SL  # noqa: E402

WINDOWS = (10, 20, 30, 60, 120)


def _harness():
    spec = importlib.util.spec_from_file_location("lam_shrink_run", HERE.parent / "lam_shrink" / "run.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


class FastLam:
    """self_loop._lam with LAM_LOOKS = window, over a record prefix that only
    grows between calls: each call reads only the lines added since the last."""

    def __init__(self, window: int):
        self.window = window
        self.n = 0
        self.fit: list[tuple] = []                  # (eid, ts, dt, changed): every fittable look
        self.mine: dict[str, list[tuple]] = {}
        self.shift: dict[str, float] = {}

    def _consume(self, prefix: list[dict]) -> None:
        if len(prefix) < self.n:
            raise ValueError("the prefix shrank: FastLam reads a record that only grows")
        for r in prefix[self.n:]:
            k = r.get("kind")
            if k == "shift" and r.get("eid") is not None:
                self.shift[r["eid"]] = r["ts"]
            elif (k == "reach" and not r.get("audit") and not r.get("audit5")
                  and r.get("answer") in ("changed", "unchanged") and r.get("dt")):
                o = (r["eid"], r["ts"], float(r["dt"]), r["answer"] == "changed")
                self.fit.append(o)
                self.mine.setdefault(r["eid"], []).append(o)
        self.n = len(prefix)

    def __call__(self, prefix: list[dict], eid: str) -> float | None:
        self._consume(prefix)
        W = self.window
        since = self.shift.get(eid)
        mine = self.mine.get(eid, [])
        obs = [o for o in mine if since is None or o[1] > since][-W:]
        if not obs:
            return None
        ll = SL._lam_ll(obs)
        if since is not None:
            prior = ([o for o in mine if o[1] <= since] or [o for o in self.fit if o[1] <= since])[-W:]
            if prior:
                mid = float(np.median([o[2] for o in prior]))
                half = 0.5 * (SL._lam_ll([(eid, 0, mid, True)]) + SL._lam_ll([(eid, 0, mid, False)]))
                ll = ll + SL.LAM_PRIOR_LOOKS * (SL._lam_ll(prior) + half) / (len(prior) + 1)
        return float(SL.LAM_GRID[int(np.argmax(ll))])


def _scored_ts(recs: list[dict]) -> dict:
    """eid -> the ts of each look the harness scores, in its order."""
    out: dict[str, list] = {}
    for r in recs:
        if (r.get("kind") == "reach" and r.get("answer") in ("changed", "unchanged")
                and not r.get("audit") and not r.get("audit5")):
            out.setdefault(r["eid"], []).append(float(r["ts"]))
    return out


def main(path, since: float | None = None) -> dict:
    H = _harness()
    recs = [json.loads(x) for x in open(path) if x.strip()]
    fits = {f"window {w}": FastLam(w) for w in WINDOWS}
    seqs = H.replay(recs, fits)
    tss = _scored_ts(recs)
    rows, per = [], {}
    for eid, s in sorted(seqs.items()):
        w = math.ceil(H.WARM * len(s))
        kept = [x for x, ts in zip(s[w:], tss[eid][w:]) if since is None or ts > since]
        rows += kept
        per[eid] = kept
    ys = np.array([x[0] for x in rows], dtype=float)
    cols = {"self's recorded p": np.array([x[1] for x in rows])}
    cols.update({name: np.array([x[2][name] for x in rows]) for name in fits})
    out = {"looks": len(rows), "things": len(per), "methods": {}, "per_thing": {}}
    for name, p in cols.items():
        b = (p - ys) ** 2
        out["methods"][name] = {"brier": round(float(b.mean()), 4), "ece": round(H.ece(p, ys), 4)}
    for eid, s in per.items():
        if len(s) < 30:
            continue
        y = np.array([x[0] for x in s], dtype=float)
        out["per_thing"][eid] = {"looks": len(s), **{name: round(float(((np.array([x[2][name] for x in s]) - y) ** 2).mean()), 4)
                                                     for name in fits}}
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    since = float(a[a.index("--since") + 1]) if "--since" in a else None
    pos = [x for i, x in enumerate(a) if not x.startswith("--") and (i == 0 or not a[i - 1].startswith("--"))]
    r = main(pos[0] if pos else HERE / "record.copy.jsonl", since)
    print(f"scored looks {r['looks']} over {r['things']} things" + (f" (after {since:.0f})" if since else ""))
    print(f"{'method':20s} {'brier':>7s} {'ece':>7s}")
    for k, m in r["methods"].items():
        print(f"{k:20s} {m['brier']:7.4f} {m['ece']:7.4f}")
    names = [f"window {w}" for w in WINDOWS]
    print("\nper thing (30+ scored looks): Brier at each window, the best marked *")
    print(f"{'thing':26s} {'looks':>5s} " + " ".join(f"{w:>7d}" for w in WINDOWS))
    for eid, t in sorted(r["per_thing"].items(), key=lambda kv: -kv[1]["looks"]):
        best = min(names, key=lambda n: t[n])
        cells = " ".join(f"{t[n]:6.4f}{'*' if n == best else ' '}" for n in names)
        print(f"{eid.split('Component:')[-1][:26]:26s} {t['looks']:5d} {cells}")
