"""RUNG 2 (#161, layer 1): the contingency ledger replayed over a REAL seat's
history -- the field's own event log and change pulses instead of a toy world.

The toy (`sandbox.py`) planted its answer, so it could not show what real data
does to the ledger. Two things, both measured on a live seat's store (10.2 h,
3682 events, 28166 change pulses):

  * THE PULSES ARE MOSTLY THE SEAT'S OWN WORDS. 76% of them (21361) are owned
    by the seat's reasoning/model turns, and 52% of all pulses are literally
    "this entity is named in that event". Replayed naively the "body" comes
    back as the act's own echo -- `tool:shell` for `exec`, `s:?entity_read`
    for `entity_read`, `s:?entity_edit` for `entity_edit` -- i.e. the things
    the act creates or names. So an answer must be owned by an event that is
    neither the act itself (its footprint) nor the seat's own voice.
  * hence acts are the agent's tool calls, changes are what OTHER events moved,
    and the ledger's own two fixes (MIN_KIND_ACTS, the p_base floor) ride in
    contingency.py, where the toy and the replay share them.

Read-only: `snapshot()` copies a live store with SQLite's own backup (the
CBLite store is SQLite under the hood), consistent while the seat keeps
writing; this module never opens the live one for writing.

    python replay.py <store-dir>          # snapshots it, then reports
"""
from __future__ import annotations

import random
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contingency as C  # noqa: E402

# the seat's own voice: its reasoning and its replies. Both are "me", neither is
# an answer to me.
SELF_NARRATION = (("discourse", "reasoning"), ("discourse", "model"))


# ---------------------------------------------------------------- the actors
def is_agent_act(d: dict) -> bool:
    """The agent's own tool call: an observation the tool role ran."""
    return d.get("stream") == "observation" and d.get("role") == "tool"


def is_user_act(d: dict) -> bool:
    """The operator speaking to the seat."""
    return d.get("stream") == "discourse" and d.get("role") == "user"


def is_machine_act(d: dict) -> bool:
    """The seat's own machinery acting without the agent: ticks, consequences,
    watch verdicts (observation, but no tool role)."""
    return d.get("stream") == "observation" and d.get("role") != "tool"


def is_narration(d: dict) -> bool:
    return (d.get("stream"), d.get("role")) in SELF_NARRATION


def kind_of(d: dict) -> str:
    return str(d.get("tool") or d.get("role") or d.get("src") or "?")


def label_of(d: dict) -> str:
    if is_agent_act(d):
        return "the agent's tool calls"
    if is_narration(d):
        return "the seat's own words"
    if is_user_act(d):
        return "the operator"
    if is_machine_act(d):
        return "the seat's machinery"
    if d.get("stream") == "discourse":
        return "notes"
    return "other"


def act_keys(events, pick=is_agent_act) -> set:
    return {k for k, d in events if pick(d)}


def acts(events, pick=is_agent_act) -> list:
    return sorted((float(d["ts"]), kind_of(d)) for _, d in events if pick(d))


def pulses(events, deltas, footprint=(), narration: bool = True) -> list:
    """The changes OTHERS made: every pulse whose owning event is not the act's
    own (its footprint) and not, by default, the seat's own words."""
    foot = set(footprint)
    ev = {k: d for k, d in events}
    out = []
    for d in deltas:
        if d.get("event") in foot:
            continue
        owner = ev.get(d.get("event"))
        if narration and owner is not None and is_narration(owner):
            continue
        out.append((float(d["ts"]), d["eid"]))
    return sorted(out)


def authors(events, deltas) -> list:
    """Who owns the change pulses -- the headline finding of rung 2."""
    ev = {k: d for k, d in events}
    c = Counter()
    for d in deltas:
        owner = ev.get(d.get("event"))
        c[label_of(owner) if owner else "<missing>"] += 1
    return c.most_common()


def mention_share(events, deltas) -> tuple:
    """How many pulses are literally 'this entity is named in that event'."""
    ev = {k: d for k, d in events}
    n_mentioned = n = 0
    for d in deltas:
        owner = ev.get(d.get("event"))
        if owner is None:
            continue
        n += 1
        if d["eid"] in {m[0] for m in (owner.get("mentions") or []) if m}:
            n_mentioned += 1
    return n_mentioned, n


# ---------------------------------------------------------------- the replay
def replay(events, deltas, window: float = 2.0, act=is_agent_act,
           narration: bool = True, shuffle_seed: int | None = None,
           min_acts: int = C.MIN_KIND_ACTS, naive: bool = False) -> dict:
    """One run of the ledger over a history: the map, and how it was built.

    `naive=True` is the replay this module exists to refuse: every pulse counts,
    including the act's own footprint and the seat's own words."""
    a = acts(events, act)
    if naive:
        ch = sorted((float(d["ts"]), d["eid"]) for d in deltas)
    else:
        ch = pulses(events, deltas, footprint=act_keys(events, act), narration=narration)
    if shuffle_seed is not None:
        r = random.Random(shuffle_seed)
        hz = max([t for t, _ in a] + [t for t, _ in ch] + [1.0])
        a = sorted((r.uniform(0, hz), k) for _, k in a)
    if not a or not ch:
        return {"acts": len(a), "changes": len(ch), "horizon": 0.0,
                "stats": {}, "cls": {"body": [], "other": [], "world": []}}
    t0 = min(min(t for t, _ in a), ch[0][0])
    hz = max(max(t for t, _ in a), ch[-1][0]) - t0
    led = C.Ledger(window=window, min_acts=min_acts)
    led.observe([(t - t0, k) for t, k in a], [(t - t0, e) for t, e in ch], horizon=hz)
    st = led.stats()
    return {"acts": len(a), "changes": len(ch), "horizon": hz, "t0": t0,
            "stats": st, "cls": C.classify(st)}


def stability(events, deltas, window: float = 2.0, **kw) -> dict:
    """The same map from each half of the history: is the body the same body?"""
    full = replay(events, deltas, window=window, **kw)
    if not full["horizon"]:
        return {"jaccard": 1.0, "first": [], "second": []}
    half = full["t0"] + full["horizon"] / 2
    ev1 = [(k, d) for k, d in events if float(d["ts"]) < half]
    ev2 = [(k, d) for k, d in events if float(d["ts"]) >= half]
    dl1 = [d for d in deltas if float(d["ts"]) < half]
    dl2 = [d for d in deltas if float(d["ts"]) >= half]
    b1 = set(replay(ev1, dl1, window=window, **kw)["cls"]["body"])
    b2 = set(replay(ev2, dl2, window=window, **kw)["cls"]["body"])
    j = len(b1 & b2) / len(b1 | b2) if b1 | b2 else 1.0
    return {"jaccard": j, "first": sorted(b1), "second": sorted(b2)}


# ------------------------------------------------------- the live seat, read-only
def snapshot(src, dst) -> Path:
    """A consistent copy of a live CBLite store, taken with SQLite's own
    backup (the store IS SQLite under the hood), so a live seat keeps writing
    into its WAL while this reads. The live store is never opened."""
    src, dst = Path(src), Path(dst)
    name = src.name if src.suffix == ".cblite" else "field.cblite"
    out = dst / name / f"{name}2" / "db.sqlite3"
    out.parent.mkdir(parents=True, exist_ok=True)
    live = src / f"{name}2" / "db.sqlite3"
    con = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    try:
        con.execute("VACUUM INTO ?", (str(out),))
    except sqlite3.OperationalError:            # older SQLite: fall back
        with sqlite3.connect(str(out)) as w:
            con.backup(w)
    finally:
        con.close()
    return out.parent.parent


def load(path, lib_dir=None) -> tuple:
    """(events, deltas) out of a CBLite store, opened READ-ONLY."""
    import os
    if lib_dir:
        os.environ.setdefault("MEMBRANE_CBL_DIR", str(lib_dir))
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from membrane.cblstore import CBLiteBackend
    b = CBLiteBackend(path, readonly=True)
    try:
        # events keep their keys (a pulse names the event that owns it); pulses
        # are just (ts, eid) rows
        return list(b.scan("events")), [d for _, d in b.scan("entity_deltas")]
    finally:
        b.close()


def report(store, windows=(2.0, 5.0, 20.0), top: int = 8) -> str:
    """The rung-2 write-up: who owns the pulses, and what each window finds."""
    if isinstance(store, tuple):
        events, deltas = store
    else:
        events, deltas = load(store)
    L = [f"# a real seat's history: {len(events)} events, {len(deltas)} change pulses", ""]
    own = authors(events, deltas)
    tot = sum(n for _, n in own)
    L.append("| who owns the pulse | pulses | share |")
    L.append("|---|---|---|")
    for k, n in own:
        L.append(f"| {k} | {n} | {100 * n / tot:.0f}% |")
    nm, na = mention_share(events, deltas)
    L += ["", f"Pulses that are literally *this entity is named in that event*: "
              f"**{nm}/{na} = {100 * nm / max(1, na):.0f}%**.", ""]
    for w in windows:
        out = replay(events, deltas, window=w)
        hz = out["horizon"] / 3600.0
        st = out["stats"]
        b = sorted(out["cls"]["body"], key=lambda e: -st[e]["z"])
        o = sorted(out["cls"]["other"], key=lambda e: -st[e]["z"])
        null = replay(events, deltas, window=w, shuffle_seed=1)["cls"]["body"]
        stb = stability(events, deltas, window=w)
        nv = replay(events, deltas, window=w, naive=True)
        L.append(f"## window {w:g}s -- {out['acts']} acts, {out['changes']} pulses, {hz:.1f} h")
        L.append(f"body **{len(b)}**, other {len(o)}, world "
                 f"{len(out['cls']['world'])}; shuffled acts find body **{len(null)}**; "
                 f"halves Jaccard **{stb['jaccard']:.2f}**")
        L.append(f"*before* the two exclusions the same window calls "
                 f"**{len(nv['cls']['body'])}** things body: " +
                 (", ".join(f"`{e}`" for e in
                            sorted(nv["cls"]["body"], key=lambda e: -nv["stats"][e]["z"])[:6])
                  or "nothing") + (" -- each the act's own echo" if nv["cls"]["body"] else ""))
        if b:
            L += ["", "| entity (body) | act | n | p_act | p_base | z |", "|---|---|---|---|---|---|"]
            for e in b[:top]:
                s = st[e]
                L.append(f"| `{e}` | {s['act']} | {s['n']} | {s['p_act']:.2f} | "
                         f"{s['p_base']:.3f} | {s['z']:.0f} |")
        if o:
            L += ["", "| entity (other) | act | n | p_act | p_base | dp |", "|---|---|---|---|---|---|"]
            for e in o[:top]:
                s = st[e]
                L.append(f"| `{e}` | {s['act']} | {s['n']} | {s['p_act']:.2f} | "
                         f"{s['p_base']:.3f} | {s['dp']:+.2f} |")
        L.append("")
    # the control: the same instrument with the operator as the actor
    out = replay(events, deltas, window=20.0, act=is_user_act)
    st = out["stats"]
    L += ["## the control: acts = the operator speaking (20s window)",
          f"{out['acts']} acts, {out['changes']} pulses -- who answers the operator?", "",
          "| entity | act | n | p_act | p_base | dp |", "|---|---|---|---|---|---|"]
    for e in sorted(set(out["cls"]["body"]) | set(out["cls"]["other"]),
                    key=lambda e: -st[e]["z"])[:top]:
        s = st[e]
        L.append(f"| `{e}` | {s['act']} | {s['n']} | {s['p_act']:.2f} | "
                 f"{s['p_base']:.3f} | {s['dp']:+.2f} |")
    return "\n".join(L)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    print(report(args[0]))
