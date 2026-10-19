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

THE TOY MEDIUM (#264): rung 7's turns with a ball. Self rolls the ball to
the other (its turn); the other can roll it back only while it is on their
side (a roll from self's side is refused). A reply is their roll after self's
turn, by the reply check. The moment self waits for is a bin of the time
since the other last acted -- their last roll of the ball or poke on any toy.

    python experiments/rung7_turns/sim.py [seeds=100] [hours=168] toy

Toy partners (here ~60 min, away ~90 min, as above):
  rhythm  the chat's turn-taker, with toys: plays (pokes a toy) every ~4 min
          while here and rolls the ball back with 0.08 + 0.8*exp(-age/180 s),
          age = how long since they last acted (engaged right after their own
          play), 0.02 away
  present rolls it back whenever here (0.85), seldom away (0.02), pokes
          another toy every ~15 min while here: presence, nothing finer
  (rhythm and present: coming back to find the ball on their side, they
  roll it back half the time)
  late    rolls it back when self's turn comes 10-30 min after they last
          acted (0.8 while here, else 0.1): the policy must learn to wait
  coin    rolls it back half the time, whatever the timing
  clock   a timer: rolls it back every 470 s on the dot when it is on their
          side, whatever self does
  echo    a machine: always rolls it back 60 s after self's turn
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


class Partner:
    """A toy-medium other (#264): see the module's docstring."""

    def __init__(self, kind, seed):
        self.kind, self.rng = kind, random.Random(seed)
        self.here = self.rng.random() < 0.5
        self.last = None           # their last act: a roll of the ball or a poke on any toy

    def p_back(self, age):
        """The chance they roll the ball back within the check, given how
        long since they last acted when self rolled it."""
        a = 1e9 if age is None else age
        if self.kind == "rhythm":
            return (0.08 + 0.8 * math.exp(-a / 180.0)) if self.here else 0.02
        if self.kind == "present":
            return 0.85 if self.here else 0.02
        if self.kind == "late":
            return (0.8 if 600.0 <= a < 1800.0 else 0.1) if self.here else 0.02
        if self.kind == "coin":
            return 0.5
        raise ValueError(self.kind)

    def step(self, t, ball_theirs):
        """One cycle of their own life: presence, toy pokes, a clock's roll.
        True when they rolled the ball back on their own."""
        dt = SL.PACE_S
        rolled = False
        if self.here and self.rng.random() < dt / 3600.0:
            self.here = False
        elif not self.here and self.rng.random() < dt / 5400.0:
            self.here = True
            if self.kind in ("rhythm", "present") and ball_theirs and self.rng.random() < 0.5:
                self.last, rolled = t, True                        # back: the ball is on my side
        play = {"rhythm": 1 / 240.0, "present": 1 / 900.0, "late": 1 / 2400.0}.get(self.kind, 0.0)
        if self.here and self.rng.random() < play * dt:
            self.last = t                                          # a poke on another toy
        if self.kind == "clock" and ball_theirs and t % 470.0 < dt:
            self.last, rolled = t, True
        return rolled


def drive_toy(kind, pusher, hours, seed):
    """#264: the rows rung 7 reads, for turns with the ball. The other's
    roll back can only come while the ball is on their side; a reply is
    their roll after self's turn, by the check."""
    o = Partner(kind, seed)
    prng = random.Random(seed * 7 + 1)
    rows, pending, aim, best = [], None, None, None
    since, dwell = SL.MEET_EVERY, [0] * NB
    theirs = False                 # the ball starts on self's side
    back_at = None                 # when they will roll it back (a reply already drawn)
    for c in range(int(hours * 3600 / SL.PACE_S)):
        t = c * SL.PACE_S
        if theirs and back_at is not None and t >= back_at:
            o.last, theirs, back_at = back_at, False, None
            if pending is not None and pending["ts"] < o.last <= pending["ts"] + SL.RUNG8_CHECK_S:
                pending["_r"] = True
        if o.step(t, theirs and back_at is None):
            theirs = False
            if pending is not None and pending["ts"] < t <= pending["ts"] + SL.RUNG8_CHECK_S:
                pending["_r"] = True
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
        theirs, back_at = True, None                               # self's turn: the ball rolls to them
        row = {"ts": t, "bin": b, "best": best, "dwell": dwell, "_r": False, "replied": None}
        rows.append(row)
        if kind == "echo":
            back_at = t + 60.0
        elif kind != "clock" and o.rng.random() < o.p_back(age):
            back_at = t + o.rng.uniform(5.0, 100.0)
        pending, since, aim, dwell = row, 0, None, [0] * NB
    return [r for r in rows if r["replied"] is not None]


def windows(rows):
    """The test at every push: (ts, window stats) for each full window."""
    d = SL._turn_deltas(rows)
    n = SL.RUNG7_PUSHES
    return [(rows[j - 1]["ts"], SL._turn_window(d[j - n:j])) for j in range(n, len(d) + 1)]


HEAD = ("other  pusher  pushes/h  pass-ever  median-h-to-pass  max-payoff-t q50/q95/q99 (contingency holding)  "
        "late reply rate vs blind")


def table(driver, cases, seeds, hours):
    """One line per (other, pusher): how often a run passes within `hours`,
    when, the best window's payoff t, and the last window's reply rates."""
    for kind, pusher in cases:
        hits, maxt, rate, rph = [], [], [], []
        for s in range(seeds):
            rows = driver(kind, pusher, hours, s)
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


def main(seeds=100, hours=168.0):
    print(f"window {SL.RUNG7_PUSHES} pushes; contingency z >= {SL.RUNG7_CONT_Z} and +{SL.RUNG7_CONT_MARGIN}; "
          f"payoff t >= {SL.RUNG7_PAY_Z} and +{SL.RUNG7_PAY_MARGIN}; explore {SL.TURN_EXPLORE}, "
          f"wait {SL.MEET_EVERY}..{SL.MEET_MAX} cycles; {seeds} seeds x {hours:.0f} h")
    print(HEAD)
    cases = [(kind, pusher) for kind in ("turn", "live", "late", "coin", "coinp")
             for pusher in ("policy", "timer", "random") if pusher == "policy" or kind in ("turn", "coin")]
    table(drive, cases, seeds, hours)


TOY_CASES = (("rhythm", "policy"), ("rhythm", "timer"), ("rhythm", "random"), ("present", "policy"),
             ("late", "policy"),
             ("coin", "policy"), ("coin", "timer"), ("clock", "policy"), ("echo", "policy"))


def main_toy(seeds=100, hours=168.0):
    print(f"#264, the toy medium (turns with a ball): window {SL.RUNG7_PUSHES} turns, the same bar; "
          f"{seeds} seeds x {hours:.0f} h")
    print(HEAD.replace("pushes/h", " turns/h"))
    table(drive_toy, TOY_CASES, seeds, hours)


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if a != "toy"]
    _main = main_toy if "toy" in sys.argv[1:] else main
    _main(int(_args[0]) if _args else 100, float(_args[1]) if len(_args) > 1 else 168.0)
