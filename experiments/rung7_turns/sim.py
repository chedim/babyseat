"""#258: is rung 7 (take turns) passable by turn-taking, and not by chance?

A world at the cycle's pace (PACE_S), driven by self_loop's OWN rung-7
functions (_turn_bin, _turn_choose, _turn_deltas, _turn_window): self's
pushes, an other who is here (~60 min) or away (~90 min) and speaks on their
own while here, and the other's replies to self's pushes. The test is read
at EVERY push of the run, like the live loop, so a run "passes" if any
window of RUNG7_PUSHES passes.

    python experiments/rung7_turns/sim.py [seeds=100] [hours=168]

Others (the chance they answer a push by the reply check, given spoke_age,
how long since they last spoke):
  turn   turn-taking: 0.08 + 0.8·exp(-age/180 s) while here, 0.03 away
  live   the live record's per-bin reply rates (replay.py): 0.62 / 0.36 /
         0.35 / 0.02 for 0-2 / 2-10 / 10-30 / 30+ min
  late   answers pushes 10-30 min after they spoke (0.8, else 0.1), speaks
         seldom (the opposite rhythm: the policy must learn to wait)
  coin   0.5, timing means nothing
  coinp  0.5 while here, 0.03 away: presence, but no timing within it
Pushers: policy (rung 7), timer (every MEET_EVERY cycles, today's), random
(a random delay at the timer's mean rate).
"""
import math
import os
import random
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import self_loop as SL  # noqa: E402

NB = len(SL.TURN_BINS) + 1
LIVE = (0.62, 0.36, 0.35, 0.02)


class Other:
    def __init__(self, kind, seed):
        self.kind, self.rng = kind, random.Random(seed)
        self.here = self.rng.random() < 0.5
        self.last = None

    def p_reply(self, age):
        a = 1e9 if age is None else age
        if self.kind == "turn":
            return (0.08 + 0.8 * math.exp(-a / 180.0)) if self.here else 0.03
        if self.kind == "live":
            return LIVE[SL._turn_bin(age)]
        if self.kind == "late":
            return (0.8 if 600.0 <= a < 1800.0 else 0.1) if self.here else 0.03
        if self.kind == "coin":
            return 0.5
        if self.kind == "coinp":
            return 0.5 if self.here else 0.03
        raise ValueError(self.kind)

    def step(self, t):
        dt = SL.PACE_S
        if self.here and self.rng.random() < dt / 3600.0:
            self.here = False
        elif not self.here and self.rng.random() < dt / 5400.0:
            self.here = True
        rate = (1 / 1200.0 if self.kind == "late" else 1 / 240.0) if self.here else 0.0
        if self.rng.random() < rate * dt:
            self.last = t


def drive(kind, pusher, hours, seed):
    """The rows rung 7 reads (bin, best, dwell, replied, ts), oldest first."""
    o = Other(kind, seed)
    prng = random.Random(seed * 7 + 1)
    rows, pending, aim, best = [], None, None, None
    since, dwell = SL.MEET_EVERY, [0] * NB
    for c in range(int(hours * 3600 / SL.PACE_S)):
        t = c * SL.PACE_S
        o.step(t)
        if pending is not None and t >= pending["ts"] + SL.RUNG8_CHECK_S:
            pending["replied"] = pending.pop("_r")
            pending = None
        since += 1
        if since < SL.MEET_EVERY:
            continue
        age = (t - o.last) if o.last is not None else None
        b = SL._turn_bin(age)
        dwell[b] += 1
        if pusher == "policy":
            if aim is None:
                aim, best = SL._turn_choose(rows, random.Random(f"{seed}:{c}"))
            go = b == aim or since >= SL.MEET_MAX
        elif pusher == "timer":
            go, best = True, 0
        else:
            go, best = prng.random() < 1.0 / SL.MEET_EVERY, 0
        if not go:
            continue
        r = o.rng.random() < o.p_reply(age)
        row = {"ts": t, "bin": b, "best": best, "dwell": dwell, "_r": r, "replied": None}
        rows.append(row)
        if r:
            o.last = t + o.rng.uniform(5.0, SL.RUNG8_CHECK_S)
        pending, since, aim, dwell = row, 0, None, [0] * NB
    return [r for r in rows if r["replied"] is not None]


def windows(rows):
    """The test at every push: (ts, window stats) for each full window."""
    d = SL._turn_deltas(rows)
    n = SL.RUNG7_PUSHES
    return [(rows[j - 1]["ts"], SL._turn_window(d[j - n:j])) for j in range(n, len(d) + 1)]


def main(seeds=100, hours=168.0):
    print(f"window {SL.RUNG7_PUSHES} pushes; contingency z >= {SL.RUNG7_CONT_Z} and +{SL.RUNG7_CONT_MARGIN}; "
          f"payoff t >= {SL.RUNG7_PAY_Z} and +{SL.RUNG7_PAY_MARGIN}; explore {SL.TURN_EXPLORE}, "
          f"wait {SL.MEET_EVERY}..{SL.MEET_MAX} cycles; {seeds} seeds x {hours:.0f} h")
    print("other  pusher  pushes/h  pass-ever  median-h-to-pass  max-payoff-t q50/q95/q99 (contingency holding)  "
          "late reply rate vs blind")
    for kind in ("turn", "live", "late", "coin", "coinp"):
        for pusher in ("policy", "timer", "random"):
            if pusher != "policy" and kind not in ("turn", "coin"):
                continue
            hits, maxt, rate, rph = [], [], [], []
            for s in range(seeds):
                rows = drive(kind, pusher, hours, s)
                rph.append(len(rows) / hours)
                ws = windows(rows)
                first = next((ts for ts, w in ws if w["passed"]), None)
                hits.append(None if first is None else first / 3600.0)
                ok = [w["payoff_z"] for _, w in ws if w["z"] >= SL.RUNG7_CONT_Z
                      and w["share"] - w["chance"] >= SL.RUNG7_CONT_MARGIN and w["payoff"] >= SL.RUNG7_PAY_MARGIN]
                maxt.append(max(ok) if ok else 0.0)
                if ws:
                    rate.append((ws[-1][1]["reply_rate"], ws[-1][1]["blind_rate"]))
            got = [h for h in hits if h is not None]
            q = np.quantile(maxt, [0.5, 0.95, 0.99])
            rr = (f"{np.mean([a for a, _ in rate]):.2f} vs {np.mean([b for _, b in rate]):.2f}" if rate else "-")
            print(f"{kind:6s} {pusher:7s} {np.median(rph):6.2f}  {len(got) / len(hits):9.2f}  "
                  f"{(np.median(got) if got else float('nan')):14.0f}  "
                  f"{q[0]:5.2f} / {q[1]:5.2f} / {q[2]:5.2f}   {rr}", flush=True)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 100, float(sys.argv[2]) if len(sys.argv) > 2 else 168.0)
