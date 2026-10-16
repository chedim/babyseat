"""RUNG 3 (#161, layer 1): how much motor channel does a body need?

Rung 2 measured a live store and found almost no motor pulses -- 53
script-authored events in ten hours carrying 163 pulses on 103 entities.
Replayed over those alone, nothing answered an act at any window. That says the
seat's motion is not recorded; it does not say how much motion a seat would have
to record before the ledger could read a body in it.

So this rung measures the INSTRUMENT's sensitivity, over the REAL act stream
(the seat's own tool calls: its kinds, its counts, its timing, and the
`MIN_KIND_ACTS` constraint rung 2 added): a PLANTED motor channel of density `q`
-- after a fraction q of the acts, a script-authored event lands `delay`
seconds later and pulses one entity -- and the ledger is asked to find it.

**This is a power analysis, not a finding.** Nothing here claims a live seat has
a body; the planted answer is ours. What comes out is a number the build can be
held to: how often a machinery-owned motion must land for the ledger to call it
a body part rather than an "other" or nothing at all.

    python density.py <store-dir>       # snapshots it, then reports
"""
from __future__ import annotations

import random
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contingency as C  # noqa: E402
import replay as R  # noqa: E402

# the planted channel rides an entity the live store never moves, so the curve
# measures the injection alone. (The first run planted on LIVE_EID, which the
# job path already pulses: its `q=0.00` control row came back as an "other" --
# the control was reading the seat, not the experiment.)
PLANT_EID = "m:body:planted"
# the one entity the live seat's machinery does move (rung 2's job path: a
# `consequence` event pulses the component that ordered the work)
LIVE_EID = "m:component:field:vitals"
MOTOR_SRC = "script:tick"

DENSITIES = (0.0, 0.02, 0.05, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 0.95)


# ------------------------------------------------------------- planting a body
def plant_motor(events, deltas, q: float = 1.0, eid: str = PLANT_EID,
                delay: float = 0.3, seed: int = 0, pick=R.is_agent_act) -> tuple:
    """A COPY of the history with a motor channel of density `q`: after a
    fraction q of the acts, a machinery event lands `delay` s later and pulses
    `eid`. Script-authored, so it is exactly what rung 2's rule lets through --
    an answer that is neither the act's own footprint nor the seat's voice."""
    ev, dl = list(events), list(deltas)
    r = random.Random(seed)
    for i, (t, _k) in enumerate(R.acts(events, pick)):
        if r.random() > q:
            continue
        key = f"v:motor:{i}"
        ev.append((key, {"stream": "observation", "role": "", "tool": "",
                         "ts": t + delay, "src": MOTOR_SRC}))
        dl.append({"eid": eid, "event": key, "ts": t + delay})
    return ev, sorted(dl, key=lambda d: d["ts"])


def eligible_kinds(events, min_acts: int = C.MIN_KIND_ACTS,
                   pick=R.is_agent_act) -> list:
    """The act kinds the ledger will even look at: (kind, n) with n >= min_acts,
    biggest first. Motion that answers any other kind is unreadable -- which is
    most of the live log's 48 kinds."""
    c = Counter(k for _, k in R.acts(events, pick))
    return sorted(((k, n) for k, n in c.items() if n >= min_acts),
                  key=lambda kn: (-kn[1], kn[0]))


def detectability(events, deltas, densities=DENSITIES, window: float = 5.0,
                  delay: float = 0.3, eid: str = PLANT_EID, seed: int = 0,
                  shuffle_seed: int = 1, pick=R.is_agent_act) -> list:
    """One row per density: plant it, ask the ledger, and ask the null too.
    `q=0.0` is the control: the history untouched."""
    rows = []
    for q in densities:
        ev, dl = plant_motor(events, deltas, q=q, eid=eid, delay=delay,
                             seed=seed, pick=pick)
        out = R.replay(ev, dl, window=window, act=pick)
        st = out["stats"].get(eid) or {}
        null = R.replay(ev, dl, window=window, act=pick, shuffle_seed=shuffle_seed)
        rows.append({
            "q": float(q),
            "acts": out["acts"], "changes": out["changes"],
            "motor_events": len(ev) - len(events),
            "motor_pulses": len(dl) - len(deltas),
            # the WHOLE map, and the planted entity's own class within it: the
            # live history has others of its own, and reading those as the
            # planted answer made the q=0.00 control claim an answer
            "body": out["cls"]["body"], "other": out["cls"]["other"],
            "planted_body": eid in set(out["cls"]["body"]),
            "planted_other": eid in set(out["cls"]["other"]),
            "p_act": st.get("p_act"), "p_base": st.get("p_base"),
            "z": st.get("z"), "lat_mad": st.get("lat_mad"),
            "null_body": null["cls"]["body"],
        })
    return rows


def busiest_machinery(events, deltas, top: int = 3) -> list:
    """The entities the seat's OWN machinery moved most: (eid, pulses)."""
    ev = {k: d for k, d in events}
    c = Counter()
    for d in deltas:
        owner = ev.get(d.get("event"))
        if owner is not None and R.is_machine_act(owner):
            c[d["eid"]] += 1
    return c.most_common(top)


def live_row(events, deltas, eid: str = LIVE_EID, window: float = 5.0,
             pick=R.is_agent_act) -> dict:
    """The ledger's own verdict on one entity of the live history (unplanted):
    what the seat's machinery already moves, and how reliably."""
    out = R.replay(events, deltas, window=window, act=pick)
    s = out["stats"].get(eid) or {}
    ev = {k: d for k, d in events}
    mine = sum(1 for d in deltas if d["eid"] == eid
               and ev.get(d.get("event")) is not None
               and R.is_machine_act(ev[d["event"]]))
    return {"eid": eid, "cls": ("body" if eid in out["cls"]["body"] else
                                "other" if eid in out["cls"]["other"] else
                                "world" if eid in out["cls"]["world"] else "absent"),
            "p_act": s.get("p_act"), "p_base": s.get("p_base"), "dp": s.get("dp"),
            "z": s.get("z"), "act": s.get("act"), "n": s.get("n"),
            "machinery_pulses": mine}


def motor_now(events, deltas) -> dict:
    """How much motor channel the history ALREADY has: pulses owned by the
    seat's own machinery (ticks, consequences, watch verdicts), and how many
    events a script authored."""
    pul, eids = 0, set()
    ev = {k: d for k, d in events}
    for d in deltas:
        owner = ev.get(d.get("event"))
        if owner is not None and R.is_machine_act(owner):
            pul += 1
            eids.add(d["eid"])
    return {
        "events": len(events), "pulses": len(deltas),
        "script_events": sum(1 for _, d in events
                             if str(d.get("src") or "").startswith("script")),
        "machine_events": sum(1 for _, d in events if R.is_machine_act(d)),
        "machine_pulses": pul, "machine_eids": len(eids),
    }


# ------------------------------------------------------------------ the write-up
def report(store, densities=DENSITIES, windows=(5.0, 20.0)) -> str:
    """Rung 3's numbers: the sensitivity curve over the real act stream at each
    window, and how far below the line the live channel sits. Given a store's
    PATH it snapshots it first (see replay.snapshot) and reads the copy.

    Both windows are shown because rung 2 read its body call at 20 s: a wider
    window is a lower bar (it catches slower answers, and a higher baseline)."""
    if isinstance(store, tuple):
        events, deltas = store
        snap = None
    else:
        snap = Path(tempfile.mkdtemp(prefix="self_l1-snap-"))
        events, deltas = R.load(R.snapshot(store, snap))
        import shutil
        shutil.rmtree(snap, ignore_errors=True)

    a = R.acts(events)
    kinds = Counter(k for _, k in a)
    elig = eligible_kinds(events)
    hz = (max(t for t, _ in a) - min(t for t, _ in a)) / 3600.0 if a else 0.0
    L = ["# rung 3: how dense must a motor channel be? -- a POWER ANALYSIS",
         "",
         "The answer the ledger is asked for here is one we planted ourselves: a",
         "script-authored pulse lands a fixed delay after a fraction `q` of the",
         "seat's real acts. The question is only how large `q` must be before the",
         "ledger stops calling it nothing (or an \"other\") and calls it a body",
         "part. Nothing in this table is a claim about the live seat.",
         ""]
    if snap is not None:
        L += ["(read from a throwaway snapshot: the live store was never opened)", ""]
    L += [f"the real act stream: **{len(a)} acts, {len(kinds)} kinds, {hz:.1f} h**; "
          f"only **{len(elig)}** kinds are exercised often enough to be read at all:",
          "", "| act kind | acts |", "|---|---|"]
    L += [f"| `{k}` | {n} |" for k, n in elig[:12]]
    L.append(f"| *{len(kinds) - len(elig)} kinds with fewer than {C.MIN_KIND_ACTS} acts* | "
             f"{sum(n for _, n in kinds.items() if n < C.MIN_KIND_ACTS)} |")
    L += ["", f"## the curve (answer delay 0.3s, one entity the store never moves)"]
    wants = []
    for w in windows:
        lines, thr = _curve(events, deltas, w, densities, hz)
        L += lines
        if thr:
            wants.append((w, thr["motor_events"] / hz if hz else 0.0))
    now = motor_now(events, deltas)
    busiest = busiest_machinery(events, deltas, top=3)
    live = live_row(events, deltas, window=windows[0])
    L += ["", "## where the live channel sits (the same history, unplanted)",
          "",
          f"events: {now['events']} ({now['script_events']} script-authored, "
          f"{now['machine_events']} machinery-owned); pulses: {now['pulses']} "
          f"({now['machine_pulses']} owned by machinery, on {now['machine_eids']} entities).",
          "",
          f"The machinery's motion is **wide and shallow**: it touches {now['machine_eids']} "
          f"entities a couple of times each, where the curve wants hundreds of motions on one. "
          f"The busiest:",
          "", "| entity the machinery moved | pulses |", "|---|---|"]
    L += [f"| `{e}` | {n} |" for e, n in busiest]
    if live["cls"] != "absent":
        L += ["", f"And the ledger's verdict on `{LIVE_EID}`, the entity rung 2's job path "
                  f"moves: **{live['cls']}** (answer to `{live['act']}`, n={live['n']}, "
                  f"p_act {_f(live['p_act'])} against a baseline of {_p(live['p_base'])}, "
                  f"dp {_f(live['dp'])}, z {_f(live['z'], 1)}, "
                  f"{live['machinery_pulses']} machinery pulses)."]
        if wants:
            w, want = wants[0]
            L.append(f"At the same {w:g}s window the curve wants ~{want:.0f} motions an hour on one "
                     f"entity; this one gets **{live['machinery_pulses'] / hz if hz else 0:.1f}**. "
                     f"It is a channel that exists -- visible, and roughly an order of magnitude "
                     f"short.")
        L.append("Its contingency may be the job path or the session's own tempo (both are driven "
                 "by the same turns): the ledger cannot tell those apart, and neither can this.")
    L += ["", "_Power analysis over a planted answer; the instrument's sensitivity, not the "
          "seat's anatomy._"]
    return "\n".join(L)


def _curve(events, deltas, window: float, densities, hz: float) -> tuple:
    """One window's table: the planted entity's own class per density."""
    rows = detectability(events, deltas, densities=densities, window=window)
    L = ["", f"### window {window:g}s",
          "| answers per act | motor events | pulses | body | other | p_act | p_base | z | null body |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        found = "yes" if r["planted_body"] else ("no" if r["planted_other"] else "--")
        L.append(f"| {r['q']:.2f} | {r['motor_events']} | {r['motor_pulses']} | "
                 f"**{found}** | {'yes' if r['planted_other'] else '--'} | "
                 f"{_f(r['p_act'])} | {_p(r['p_base'])} | {_f(r['z'], 1)} | "
                 f"{len(r['null_body'])} |")
    thr = next((r for r in rows if r["planted_body"]), None)
    oth = next((r for r in rows if r["planted_other"] and not r["planted_body"]), None)
    L.append("")
    if oth:
        L.append(f"At **{oth['q']:.0%}** of the acts answered the ledger sees something "
                 f"that responds but unreliably -- an *other* (p_act {_f(oth['p_act'])}, "
                 f"p_base {_p(oth['p_base'])}), not a body part.")
    if thr:
        per_h = thr["motor_events"] / hz if hz else 0.0
        L.append(f"A planted answer is called **body** only at **{thr['q']:.0%} of the acts** "
                 f"(p_act {_f(thr['p_act'])}, p_base {_p(thr['p_base'])}, z {_f(thr['z'], 0)}, "
                 f"delay MAD {_f(thr['lat_mad'], 2)}s), i.e. **{thr['motor_events']} machinery "
                 f"events on ONE entity over {hz:.1f} h -- about {per_h:.0f} an hour**, and only "
                 f"if they serve an eligible act kind.")
        if thr["p_act"] and thr["p_act"] > thr["q"] + 0.05:
            L.append(f"Note p_act ({_f(thr['p_act'])}) runs above q ({thr['q']:.2f}): the seat "
                     f"acts in bursts, so an answer to a neighbouring act lands inside this "
                     f"act's window too -- the stray rung 1's median absolute deviation exists "
                     f"for. The honest threshold is the one in events per hour.")
    else:
        L.append("No density in this sweep is called body.")
    return L, thr


def _f(x, nd: int = 2) -> str:
    return "--" if x is None else f"{float(x):.{nd}f}"


def _p(x) -> str:
    """A baseline small enough to be the horizon's own floor is not a zero; an
    exact zero is (nothing moved outside the act windows)."""
    if x is None:
        return "--"
    if not float(x):
        return "0"
    return f"{float(x):.1e}" if float(x) < 0.001 else f"{float(x):.4f}"


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if not x.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    print(report(args[0]))
