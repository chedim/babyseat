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

THE BALL'S OWN CLOCK (#278): drive_toy runs the loop's toy rules and
constants -- bins TOY_TURN_BINS (0-20 s, 20-60, 1-3 min, 3+), self rolls no
sooner than TOY_MEET_EVERY cycles after its last roll, never before that
roll's check, and only once the ball is back on its side (TOY_MEET_MAX
fetches an unreturned one); the check comes the cycle self sees the ball
back, or TOY_CHECK_S after the roll; a reply is a roll within TOY_CHECK_S;
only the cycles self could roll in count as its waiting time. The bar is
the toy clock's (TOY_RUNG7_*).

    python experiments/rung7_turns/sim.py [seeds=100] [hours=168] toy [jobs=N]

Toy partners (here ~60 min, away ~90 min, unless said; a roll back comes
5-20 s after self's):
  rhythm  the chat's turn-taker, with toys: plays (pokes a toy) every ~4 min
          while here and rolls the ball back with 0.08 + 0.8*exp(-age/60 s),
          age = how long since they last acted (engaged right after their own
          act), 0.02 away
  present rolls it back whenever here (0.85), seldom away (0.02), pokes
          another toy every ~15 min while here: presence, nothing finer
  (rhythm and present: coming back to find the ball on their side, they
  roll it back half the time)
  late    rolls it back when self's turn comes 1-3 min after they last
          acted (0.8 while here, else 0.1): the policy must learn to wait
  session a person who plays in sessions: 1-2 h at the dock, then away 4-10 h;
          while there rolls it back 0.9 of the time and pokes another toy
          every ~3 min; sitting down to find the ball on their side, rolls it
          back; never while away
  coin    rolls it back half the time, whatever the timing
  clock   a timer: rolls it back every 470 s on the dot when it is on their
          side, whatever self does
  metronome  the same every 47 s: next to the check, its phase is learnable
  poisson a memoryless returner: rolls it back at random, ~1 a minute, while
          it is on their side, whatever self does
  echo    a machine: always rolls it back 10 s after self's turn
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


def _c(s):
    """#343: the loop's gaps are seconds now; the sim steps cycles at PACE_S (10 s)."""
    return int(round(s / SL.PACE_S))


def drive(kind, pusher, hours, seed):
    """The rows rung 7 reads (bin, best, dwell, replied, ts), oldest first."""
    o = Other(kind, seed)
    prng = random.Random(seed * 7 + 1)
    rows, pending, aim, best = [], None, None, None
    every, cap = _c(SL.MEET_EVERY), _c(SL.MEET_MAX)
    since, dwell = every, [0] * NB
    for c in range(int(hours * 3600 / SL.PACE_S)):
        t = c * SL.PACE_S
        o.step(t)
        if pending is not None and t >= pending["ts"] + SL.RUNG8_CHECK_S:
            pending["replied"] = pending.pop("_r")
            pending = None
        since += 1
        if since < every:
            continue
        age = (t - o.last) if o.last is not None else None
        b = SL._turn_bin(age)
        dwell[b] += 1
        if pusher == "policy":
            if aim is None:
                aim, best = SL._turn_choose(rows, random.Random(f"{seed}:{c}"))
            go = b == aim or since >= cap
        elif pusher == "timer":
            go, best = True, 0
        else:
            go, best = prng.random() < 1.0 / every, 0
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
    """A toy-medium other (#264, on the ball's own clock since #278): see the
    module's docstring."""

    def __init__(self, kind, seed):
        self.kind, self.rng = kind, random.Random(seed)
        self.here = self.rng.random() < 0.5
        self.last = None           # their last act: a roll of the ball or a poke on any toy
        self.here_s = 0.0          # how long they have been at the dock so far (play time)
        if kind == "session":      # a play session of 1-2 h, then away for 4-10 h
            self.here = self.rng.random() < 0.2
            self.flip = self.rng.uniform(3600.0, 7200.0) if self.here else self.rng.uniform(0.0, 36000.0)

    def p_back(self, age):
        """The chance they roll the ball back within the check, given how
        long since they last acted when self rolled it."""
        a = 1e9 if age is None else age
        if self.kind == "rhythm":
            return (0.08 + 0.8 * math.exp(-a / 60.0)) if self.here else 0.02
        if self.kind == "present":
            return 0.85 if self.here else 0.02
        if self.kind == "late":
            return (0.8 if 60.0 <= a < 180.0 else 0.1) if self.here else 0.02
        if self.kind == "coin":
            return 0.5
        if self.kind == "session":
            return 0.9 if self.here else 0.0
        raise ValueError(self.kind)

    def delay(self):
        """How long after self's roll their roll back comes (inside the check)."""
        return self.rng.uniform(5.0, 20.0)

    def step(self, t, ball_theirs):
        """One cycle of their own life: presence, toy pokes, a clock's roll.
        True when they rolled the ball back on their own."""
        dt = SL.PACE_S
        rolled = False
        if self.here:
            self.here_s += dt
        if self.kind == "session":
            if t >= self.flip:
                self.here = not self.here
                self.flip = t + (self.rng.uniform(3600.0, 7200.0) if self.here else self.rng.uniform(14400.0, 36000.0))
                if self.here and ball_theirs:
                    self.last, rolled = t, True                    # sat down to play: the ball comes back
        elif self.here and self.rng.random() < dt / 3600.0:
            self.here = False
        elif not self.here and self.rng.random() < dt / 5400.0:
            self.here = True
            if self.kind in ("rhythm", "present") and ball_theirs and self.rng.random() < 0.5:
                self.last, rolled = t, True                        # back: the ball is on my side
        play = {"rhythm": 1 / 240.0, "present": 1 / 900.0, "late": 1 / 2400.0, "session": 1 / 180.0}.get(self.kind, 0.0)
        if self.here and self.rng.random() < play * dt:
            self.last = t                                          # a poke on another toy
        period = CLOCKS.get(self.kind)
        if period and ball_theirs and t % period < dt:
            self.last, rolled = t, True
        if self.kind == "poisson" and ball_theirs and self.rng.random() < dt / 60.0:
            self.last, rolled = t, True                            # memoryless: whenever, ~1 a minute
        return rolled


CLOCKS = {"clock": 470.0, "metronome": 47.0}     # a player on a timer: rolls it back every this many s


def drive_toy(kind, pusher, hours, seed):
    """#264, on the ball's own clock (#278): the rows rung 7 reads, for turns
    with the ball, by the loop's rules -- self rolls no sooner than
    TOY_MEET_EVERY cycles after its last roll, never before that roll's check,
    and only with the ball back on its side (the cap, TOY_MEET_MAX, fetches an
    unreturned one); the check comes the cycle self sees the ball back, or
    TOY_CHECK_S after the roll; a reply is their roll within TOY_CHECK_S of
    self's. Only the cycles self could roll in count as dwell. Each row also
    carries `age` (the other's last act, as self knew it at the turn) and
    `play_h` (the hours they had been at the dock by then)."""
    o = Partner(kind, seed)
    prng = random.Random(seed * 7 + 1)
    every, cap, check = _c(SL.TOY_MEET_EVERY), _c(SL.TOY_MEET_MAX), SL.TOY_CHECK_S
    bins = SL.TOY_TURN_BINS
    rows, pending, aim, best = [], None, None, None
    last_c, dwell = -cap, [0] * NB
    theirs = False                 # the ball starts on self's side
    back_at = None                 # when they will roll it back (a reply already drawn)
    for c in range(int(hours * 3600 / SL.PACE_S)):
        t = c * SL.PACE_S
        if theirs and back_at is not None and t >= back_at:
            o.last, theirs, back_at = back_at, False, None
            if pending is not None and pending["ts"] < o.last <= pending["ts"] + check:
                pending["_r"] = True
        if o.step(t, theirs and back_at is None):
            theirs = False
            if pending is not None and pending["ts"] < t <= pending["ts"] + check:
                pending["_r"] = True
        if pending is not None and (not theirs or t >= pending["ts"] + check):
            pending["replied"] = pending.pop("_r")             # the check: the ball seen back, or the horizon
            pending = None
        if pending is not None:
            continue                                           # never before the last roll's check
        since = c - last_c
        if since < every or (theirs and since < cap):
            continue                                           # too soon, or the ball is with them
        age = (t - o.last) if o.last is not None else None
        b = SL._turn_bin(age, bins)
        dwell[b] += 1
        if pusher == "policy":
            if aim is None:
                aim, best = SL._turn_choose(rows, random.Random(f"{seed}:{c}"))
            go = b == aim or since >= cap
        elif pusher == "timer":
            go, best = True, 0
        else:
            go, best = prng.random() < 1.0 / every or since >= cap, 0
        if not go:
            continue
        theirs, back_at = True, None                               # self's turn: the ball rolls to them
        row = {"ts": t, "bin": b, "best": best, "dwell": dwell, "_r": False, "replied": None,
               "clock": SL.TOY_CLOCK, "age": age, "play_h": o.here_s / 3600.0}
        rows.append(row)
        if kind == "echo":
            back_at = t + 10.0
        elif kind not in CLOCKS and kind != "poisson" and o.rng.random() < o.p_back(age):
            back_at = t + o.delay()
        pending, last_c, aim, dwell = row, c, None, [0] * NB
    return [r for r in rows if r["replied"] is not None]


def windows(rows):
    """The test at every push: (ts, window stats) for each full window."""
    d = SL._turn_deltas(rows)
    n = SL.RUNG7_PUSHES
    clock = rows[0].get("clock", 1) if rows else 1                 # #278: the bar of the rows' clock
    return [(rows[j - 1]["ts"], SL._turn_window(d[j - n:j], clock)) for j in range(n, len(d) + 1)]


HEAD = ("other  pusher  pushes/h  pass-ever  median-h-to-pass  max-payoff-t q50/q95/q99 (contingency holding)  "
        "late reply rate vs blind")


def _one(args):
    """One run: (hours to the first pass or None, play-hours by then or None,
    the best window's payoff t where the contingency holds, the last window's
    (reply rate, blind rate) or None, pushes an hour)."""
    driver, kind, pusher, hours, s = args
    rows = driver(kind, pusher, hours, s)
    ws = windows(rows)
    ck = SL._clock(rows[0].get("clock", 1) if rows else 1)
    first = next(((ts, j) for j, (ts, w) in enumerate(ws) if w["passed"]), None)
    play = None
    if first is not None and "play_h" in rows[0]:
        play = next(r["play_h"] for r in rows if r["ts"] == first[0])
    ok = [w["payoff_z"] for _, w in ws if w["z"] >= ck["cont_z"]
          and w["share"] - w["chance"] >= ck["cont_margin"] and w["payoff"] >= ck["pay_margin"]]
    rate = (ws[-1][1]["reply_rate"], ws[-1][1]["blind_rate"]) if ws else None
    return (None if first is None else first[0] / 3600.0, play, max(ok) if ok else 0.0, rate, len(rows) / hours)


def table(driver, cases, seeds, hours, jobs=1, play=False):
    """One line per (other, pusher): how often a run passes within `hours`,
    when, the best window's payoff t, and the last window's reply rates
    (with `play`: also the median play-hours -- hours the other was at the
    dock -- to the pass)."""
    pool = None
    if jobs > 1:
        import multiprocessing as mp
        pool = mp.get_context("fork").Pool(jobs)
    try:
        for kind, pusher in cases:
            args = [(driver, kind, pusher, hours, s) for s in range(seeds)]
            res = pool.map(_one, args) if pool else [_one(a) for a in args]
            hits = [r[0] for r in res]
            got = [h for h in hits if h is not None]
            plays = [r[1] for r in res if r[1] is not None]
            q = np.quantile([r[2] for r in res], [0.5, 0.95, 0.99])
            rate = [r[3] for r in res if r[3] is not None]
            rr = (f"{np.mean([a for a, _ in rate]):.2f} vs {np.mean([b for _, b in rate]):.2f}" if rate else "-")
            line = (f"{kind:8s} {pusher:7s} {np.median([r[4] for r in res]):6.2f}  {len(got) / len(hits):9.2f}  "
                    f"{(np.median(got) if got else float('nan')):14.1f}  "
                    f"{q[0]:5.2f} / {q[1]:5.2f} / {q[2]:5.2f}   {rr}")
            if play:
                line += f"   play-h {(np.median(plays) if plays else float('nan')):.1f}"
            print(line, flush=True)
    finally:
        if pool:
            pool.close()


def main(seeds=100, hours=168.0):
    print(f"window {SL.RUNG7_PUSHES} pushes; contingency z >= {SL.RUNG7_CONT_Z} and +{SL.RUNG7_CONT_MARGIN}; "
          f"payoff t >= {SL.RUNG7_PAY_Z} and +{SL.RUNG7_PAY_MARGIN}; explore {SL.TURN_EXPLORE}, "
          f"wait {SL.MEET_EVERY:g}..{SL.MEET_MAX:g} s; {seeds} seeds x {hours:.0f} h")
    print(HEAD)
    cases = [(kind, pusher) for kind in ("turn", "live", "late", "coin", "coinp")
             for pusher in ("policy", "timer", "random") if pusher == "policy" or kind in ("turn", "coin")]
    table(drive, cases, seeds, hours)


TOY_CASES = (("rhythm", "policy"), ("rhythm", "timer"), ("rhythm", "random"), ("present", "policy"),
             ("late", "policy"), ("session", "policy"),
             ("coin", "policy"), ("coin", "timer"), ("clock", "policy"), ("metronome", "policy"),
             ("poisson", "policy"), ("echo", "policy"))


def main_toy(seeds=100, hours=168.0, jobs=1):
    ck = SL._clock(SL.TOY_CLOCK)
    print(f"#278, the toy medium on the ball's own clock: window {SL.RUNG7_PUSHES} turns; contingency z >= "
          f"{ck['cont_z']} and +{ck['cont_margin']}; payoff t >= {ck['pay_z']} and +{ck['pay_margin']}; "
          f"bins {SL.TOY_TURN_BINS} s, wait {SL.TOY_MEET_EVERY:g}..{SL.TOY_MEET_MAX:g} s, check {SL.TOY_CHECK_S} s; "
          f"{seeds} seeds x {hours:.0f} h")
    print(HEAD.replace("pushes/h", " turns/h") + "  play-h-to-pass")
    table(drive_toy, TOY_CASES, seeds, hours, jobs=jobs, play=True)


if __name__ == "__main__":
    _args = [a for a in sys.argv[1:] if a != "toy" and not a.startswith("jobs=")]
    _jobs = next((int(a[5:]) for a in sys.argv[1:] if a.startswith("jobs=")), 1)
    if "toy" in sys.argv[1:]:
        main_toy(int(_args[0]) if _args else 100, float(_args[1]) if len(_args) > 1 else 168.0, _jobs)
    else:
        main(int(_args[0]) if _args else 100, float(_args[1]) if len(_args) > 1 else 168.0)
