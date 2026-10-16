"""#161 RUNG 4 (layer 1): WHAT should a motion move? -- the design probe.

Rung 3 gave the motor channel a rate (~40-60 motions an hour on one entity).
This one asks the design question that follows: a motion pulse has to move
*something* -- which entity? Two obvious answers, simulated on the live history
by planting a machinery-owned pulse after every agent act:

  * **the argument** -- the entity the act is about (the event's top `coords`).
    Over 10.1 h this is a DENSE channel (5457 pulses) and it still reads as
    **no body at all**: 40 others. Each entity answers only its share of a
    kind's acts (p_act 0.17-0.65), and a body part must answer every act of
    one kind. The body is an INVARIANT of the action, not an argument of it.
  * **the tool** -- the tool entity of the act that ran (`tool:exec` for exec).
    That reads as **8 body parts** at p_act 1.00, p_base 0 -- and it is a
    tautology: we pulsed `tool:exec` for every exec, so the ledger "discovers"
    exactly what the recorder asserted. It is rung 2's act echo, rebuilt by
    hand.

The lesson, and the reason this file exists: **the ledger cannot tell a
recorded tautology from a body -- the shuffled null does not catch it either**
(both pinned by tests below). Only the recorder can, by pulsing a change that
could have failed to happen.

    python probe.py <store-dir>       # snapshots it, then reports
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import density as D  # noqa: E402
import replay as R  # noqa: E402

MOTOR_SRC = "script:consequence"     # what a landing would be authored by
CHANNELS = ("coord", "tool")


def target_of(d: dict, channel: str) -> str | None:
    """The entity a naive recorder would pulse for this act."""
    if channel == "coord":                 # ...the entity the act is about
        c = d.get("coords") or []
        return c[0][0] if c and c[0] else None
    if channel == "tool":                  # ...the tool the act ran
        t = str(d.get("tool") or "")
        return f"tool:{t}" if t else None
    raise ValueError(channel)


def plant_channel(events, deltas, channel: str, delay: float = 0.3,
                  src: str = MOTOR_SRC) -> tuple:
    """A copy of the history with one naive motor channel planted after every
    agent act. Machinery-authored, and it lands inside the act's window -- so
    rung 2's two rules (not the act's footprint, not the seat's voice) let it
    through, exactly as a real recorder's pulses would be."""
    ev, dl = list(events), list(deltas)
    for i, (k, d) in enumerate(events):
        if not R.is_agent_act(d):
            continue
        eid = target_of(d, channel)
        if not eid:
            continue
        t = float(d["ts"]) + delay
        key = f"v:probe:{channel}:{i}"
        ev.append((key, {"stream": "observation", "role": "", "tool": "",
                         "ts": t, "src": src}))
        dl.append({"eid": eid, "event": key, "ts": t})
    return ev, sorted(dl, key=lambda x: x["ts"])


def probe(events, deltas, channels=CHANNELS, window: float = 5.0,
          shuffle_seed: int = 1) -> list:
    """One row per naive channel: what the ledger makes of it."""
    rows = []
    for c in channels:
        ev, dl = plant_channel(events, deltas, c)
        out = R.replay(ev, dl, window=window)
        st = out["stats"]
        rows.append({
            "channel": c, "acts": out["acts"], "pulses": out["changes"],
            "planted": len(ev) - len(events),
            "body": sorted(out["cls"]["body"], key=lambda e: -st[e]["z"]),
            "other": sorted(out["cls"]["other"], key=lambda e: -st[e]["z"]),
            "null_body": R.replay(ev, dl, window=window,
                                  shuffle_seed=shuffle_seed)["cls"]["body"],
            "stats": st,
        })
    return rows


def report(store, window: float = 5.0, top: int = 6) -> str:
    """The design probe's table. Given a store's PATH it snapshots it first
    (see replay.snapshot) and reads the copy."""
    if isinstance(store, tuple):
        events, deltas = store
    else:
        snap = Path(tempfile.mkdtemp(prefix="self_l1-snap-"))
        events, deltas = R.load(R.snapshot(store, snap))
        import shutil
        shutil.rmtree(snap, ignore_errors=True)
    L = ["# rung 4: what should a motion move? -- the DESIGN PROBE",
         "",
         "A motion pulse has to move something. Two obvious answers, planted after",
         "every agent act over this history (machinery-authored, landing inside the",
         "act's window, so rung 2's rules let them through as a real recorder's",
         "pulses would be). Nothing here is a claim about the live seat: the",
         "channels are ours.",
         ""]
    for r in probe(events, deltas, window=window):
        what = ("the entity the act is about (its top `coords`)" if r["channel"] == "coord"
                else "the tool the act ran (its own echo)")
        L += [f"## channel: pulse {what} -- {r['planted']} planted pulses",
              f"body **{len(r['body'])}**, other {len(r['other'])}; "
              f"shuffled null finds body **{len(r['null_body'])}**", ""]
        pool = (r["body"] or r["other"])[:top]
        if pool:
            L += ["| entity | class | act | n | p_act | p_base | z |",
                  "|---|---|---|---|---|---|---|"]
            for e in pool:
                s = r["stats"][e]
                L.append(f"| `{e}` | {'**body**' if e in r['body'] else 'other'} | "
                         f"{s['act']} | {s['n']} | {s['p_act']:.2f} | "
                         f"{s['p_base']:.3f} | {s['z']:.0f} |")
        L.append("")
    L += ["## what the probe says",
          "",
          "- **The argument is not a body.** The densest possible channel (every",
          "  act, every hour: 540 pulses/h) reads as *no body at all*, because each",
          "  entity answers only its share of a kind's acts. A body part answers",
          "  **every** act of one kind: it is an invariant of the action.",
          "- **The tool is a tautology.** Pulsing `tool:<the act>` makes the tool",
          "  entities body parts at p_act 1.00 -- the ledger confirming what the",
          "  recorder asserted. That is rung 2's act echo, hand-built.",
          "- **The null does not catch it.** The shuffled-act control finds no body",
          "  for either channel, including the tautology: the ledger cannot audit our",
          "  bookkeeping from the outside. The rule has to be the recorder's --",
          "  pulse a change that could have failed to happen."]
    return "\n".join(L)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    print(report(args[0]))
