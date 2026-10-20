"""#291: open loops, closure and rest -- a mechanism toy.

A tracker lives through days minute by minute. Episodes start and end while
it is awake; at an episode's end it sees an ending cue (0..1) and closes the
episode only if the cue clears its CLOSURE THRESHOLD -- a dial of
difference, not damage. An episode closed at its end settles at once; one
left open is held: it can still close later (slowly, and only with spare
capacity) or in rest. Every held episode keeps arousal up against rest
pressure, which builds while awake; sleep comes once pressure clears
arousal inside the night window. Holding past the point rest would have
come costs CAPACITY, and more the longer it lasts (the cost grows with the
pressure held off); low capacity makes endings harder to recognize, so a
tired tracker leaves more open -- the feedback loop. At zero capacity rest
arrives on its own. Rest restores capacity and settles most of what is held.

THE CHANNEL (the second dial): a settled episode doesn't land at once -- it
goes through a channel of CHANNEL units an hour (None: wide, at once), and
each episode is DETAIL units (a tracker that keeps more of each episode
sends more). What waits is the BACKLOG: it holds arousal like a held
episode, a little per unit, and sending costs capacity while awake (rest
sends REST_CHANNEL times faster, and free) -- so a rich tracker behind a
narrow channel pays by day, which the first dial never does.

Two gentle helps:
  offload  writing it down: at bedtime most held episodes are parked
           (held elsewhere), so they stop holding arousal; rest settles
           them like the rest, and any left come back in the morning
  clear    clear endings: every ending cue is stronger

    python -I experiments/open_loops/sim.py [seeds=50] [days=14] [channel]

It models no person and no condition: see README.md.
"""
from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field

DAY = 1440
WAKE = 7 * 60               # the alarm, 07:00
BED = 23 * 60               # bedtime, 23:00
P_BED = 16.0                # rest pressure at bedtime after a 16 h day (hours awake)
P_UP = 1 / 60               # pressure gained per awake minute
P_DOWN = 2 / 60             # pressure lost per minute of rest (8 h of rest clears a day)
AROUSAL = 0.5               # arousal per held episode, in hours of pressure it holds off
HOLD = 0.0008               # capacity per minute, per held episode, per hour of pressure held off
RESTORE = 1 / 240           # capacity regained per minute of rest (4 h from empty to full)
FATIGUE = 0.3               # an empty tracker's threshold is this much higher
EPISODES_PER_H = 2.0        # episodes starting per awake hour (until an hour before bedtime)
EPISODE_MIN = 20.0          # their mean length, minutes
LATE = 0.006                # per awake minute, a held episode's chance to close later (x capacity)
REST_SETTLES = 0.6          # a held (or parked) episode's chance to settle in a night's rest
OFFLOAD = 0.7               # writing it down: the share of held episodes parked at bedtime
CLEAR = 0.3                 # clear endings: added to every ending cue
RESTED = 2.0                # a rest that arrived by day ends once pressure is down to this
REST_CHANNEL = 4.0          # the channel runs this many times faster in rest
SEND_COST = 0.002           # capacity per unit sent while awake
BACKLOG_AROUSAL = 0.1       # hours of pressure each unit of backlog holds off


@dataclass
class State:
    pressure: float = 0.0
    capacity: float = 1.0
    asleep: bool = False
    open: list = field(default_factory=list)      # held episodes (their end times)
    parked: list = field(default_factory=list)    # written down: held elsewhere
    running: list = field(default_factory=list)   # episodes under way (their end times)
    queue: float = 0.0                              # the backlog: units waiting for the channel


def threshold(st: State, theta: float) -> float:
    """The closure threshold as it stands: the dial, raised as capacity runs low."""
    return theta + FATIGUE * (1.0 - st.capacity)


def arousal(st: State) -> float:
    return AROUSAL * len(st.open) + BACKLOG_AROUSAL * st.queue


def drain(st: State, channel: float | None, asleep: bool) -> tuple[float, float]:
    """One minute of the channel: (units sent, capacity it cost)."""
    if channel is None or st.queue <= 0:
        return 0.0, 0.0
    moved = min(st.queue, channel / 60 * (REST_CHANNEL if asleep else 1.0))
    st.queue -= moved
    return moved, 0.0 if asleep else SEND_COST * moved


def hold_cost(st: State) -> float:
    """Capacity spent this minute holding episodes open: nothing before rest
    would have come, then more the further pressure is held off."""
    over = st.pressure - P_BED
    return HOLD * len(st.open) * over if over > 0 and st.open else 0.0


def _night(m: int) -> bool:
    return m >= BED or m < WAKE


def simulate(theta: float, days: int = 14, seed: int = 0, offload: bool = False, clear: bool = False,
             channel: float | None = None, detail: float = 1.0) -> dict:
    """`days` days from the first 07:00; one row per night (from its bedtime)."""
    rng = random.Random(seed)
    st = State()
    rows: list[dict] = []
    row = None
    ended = settled_at_end = 0
    peak = 0.0

    def settle(n: int = 1) -> None:
        if channel is not None:
            st.queue += n * detail

    def fall_asleep() -> None:                        # rest settles most of what is held
        st.asleep = True
        for pile in ("open", "parked"):
            left = [e for e in getattr(st, pile) if rng.random() >= REST_SETTLES]
            settle(len(getattr(st, pile)) - len(left))
            setattr(st, pile, left)
    for t in range(WAKE, WAKE + days * DAY):
        m = t % DAY
        if m == BED:                                  # bedtime: the night's row opens
            if offload:
                keep = []
                for e in st.open:
                    (st.parked if rng.random() < OFFLOAD else keep).append(e)
                st.open = keep
            row = {"day": len(rows), "open_at_bed": len(st.open),        # what still holds arousal
                   "onset_min": None, "sleep_min": 0, "rest_arrived": 0,
                   "settled_at_end_share": settled_at_end / ended if ended else 1.0,
                   "backlog_at_bed": st.queue, "peak_backlog": peak}
            rows.append(row)
            ended = settled_at_end = 0
            peak = st.queue
        peak = max(peak, st.queue)
        if st.asleep:
            drain(st, channel, asleep=True)
            st.pressure = max(0.0, st.pressure - P_DOWN)
            st.capacity = min(1.0, st.capacity + RESTORE)
            if row is not None:
                row["sleep_min"] += 1
            woke = (m == WAKE) if _night(m) or m == WAKE else st.pressure <= RESTED
            if woke:
                st.asleep = False
                st.open.extend(st.parked)                # what rest left comes back held
                st.parked = []
                if m == WAKE and row is not None:
                    row["capacity_morning"] = st.capacity
                    row["backlog_morning"] = st.queue
            continue
        # awake
        st.pressure += P_UP
        if WAKE <= m < BED - 60 and rng.random() < EPISODES_PER_H / 60:
            st.running.append(t + max(1, int(rng.expovariate(1 / EPISODE_MIN))))
        done = [e for e in st.running if e <= t]
        st.running = [e for e in st.running if e > t]
        for e in done:                                # an ending: recognized, or held
            ended += 1
            cue = rng.random() + (CLEAR if clear else 0.0)
            if cue >= threshold(st, theta):
                settled_at_end += 1
                settle()
            else:
                st.open.append(e)
        still = [e for e in st.open if rng.random() >= LATE * st.capacity]
        settle(len(st.open) - len(still))
        st.open = still
        _, cost = drain(st, channel, asleep=False)
        st.capacity = max(0.0, st.capacity - hold_cost(st) - cost)
        if st.capacity <= 0.0:                        # rest arrives on its own
            fall_asleep()
            if row is not None:
                row["rest_arrived"] += 1
                if row["onset_min"] is None and _night(m):
                    row["onset_min"] = (t - BED) % DAY
            continue
        if _night(m) and st.pressure - arousal(st) >= P_BED - 1e-6:
            fall_asleep()
            if row is not None and row["onset_min"] is None:
                row["onset_min"] = (t - BED) % DAY
    for r in rows:                                    # a night with no sleep at all: the whole night
        if r["onset_min"] is None:
            r["onset_min"] = DAY - (BED - WAKE)
        r.setdefault("capacity_morning", st.capacity)
        r.setdefault("backlog_morning", st.queue)
    return {"theta": theta, "offload": offload, "clear": clear, "channel": channel, "detail": detail,
            "seed": seed, "days": rows}


def summary(runs: list[dict]) -> dict:
    ds = [d for r in runs for d in r["days"]]
    n = len(ds)
    return {"onset_h": sum(d["onset_min"] for d in ds) / n / 60,
            "sleep_h": sum(d["sleep_min"] for d in ds) / n / 60,
            "open_at_bed": sum(d["open_at_bed"] for d in ds) / n,
            "rest_arrived_per_wk": 7 * sum(d["rest_arrived"] for d in ds) / n,
            "settled_at_end": sum(d["settled_at_end_share"] for d in ds) / n,
            "backlog_at_bed": sum(d["backlog_at_bed"] for d in ds) / n,
            "peak_backlog": sum(d["peak_backlog"] for d in ds) / n}


def main(argv: list[str]) -> None:
    seeds = int(argv[0]) if argv else 50
    days = int(argv[1]) if len(argv) > 1 else 14
    if len(argv) > 2 and argv[2] == "channel":
        return channel_table(seeds, days)
    print(f"{seeds} seeds x {days} days; means per night")
    print(f"{'threshold':>9} {'help':>8} {'to sleep h':>10} {'rest h':>7} {'open at bed':>11} "
          f"{'rest arrived /wk':>16} {'settled at end':>14}")
    for theta in (0.1, 0.3, 0.5, 0.7, 0.9):
        for name, kw in (("none", {}), ("offload", {"offload": True}), ("clear", {"clear": True})):
            s = summary([simulate(theta, days=days, seed=k, **kw) for k in range(seeds)])
            print(f"{theta:>9.1f} {name:>8} {s['onset_h']:>10.2f} {s['sleep_h']:>7.2f} {s['open_at_bed']:>11.2f} "
                  f"{s['rest_arrived_per_wk']:>16.2f} {s['settled_at_end']:>14.2f}")


def channel_table(seeds: int, days: int) -> None:
    print(f"{seeds} seeds x {days} days; means per night; channel in units/h (wide = at once)")
    print(f"{'threshold':>9} {'detail':>6} {'channel':>7} {'to sleep h':>10} {'open at bed':>11} "
          f"{'rest arrived /wk':>16} {'peak backlog':>12} {'backlog at bed':>14}")
    for theta in (0.3, 0.6):
        for detail in (1.0, 3.0):
            for channel in (None, 8.0, 4.0):
                s = summary([simulate(theta, days=days, seed=k, channel=channel, detail=detail)
                             for k in range(seeds)])
                ch = "wide" if channel is None else f"{channel:g}"
                print(f"{theta:>9.1f} {detail:>6g} {ch:>7} {s['onset_h']:>10.2f} {s['open_at_bed']:>11.2f} "
                      f"{s['rest_arrived_per_wk']:>16.2f} {s['peak_backlog']:>12.2f} {s['backlog_at_bed']:>14.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
