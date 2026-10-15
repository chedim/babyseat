"""THE IDIOMS (2026-10-15): the seat's acts read as a language.

An act is the program a call runs (grep, git, sed...; the tool itself for calls
that run no command). Within a session (an unlabeled stream splits at 30-minute
gaps), every step from one act to a DIFFERENT act is counted, and an idiom is a
step that happens more often than chance: lift = P(b | a) / P(b). The gate (two
seats' real fields, time split, shuffle-within-session null): order carries
structure beyond burstiness (+0.31 bits/act on one seat, null -0.11) and reads as
idioms -- grep -> read, grep <-> sed, python -> git, entity_add -> entity_edit ->
entity_watch. Recording only: an idiom never runs anything."""
from __future__ import annotations

import re
from collections import Counter

GAP = 1800.0          # an unlabeled stream's session ends after this quiet


def act_name(ev: dict) -> str | None:
    """The act a tool call is, or None for what is no act (the heartbeat)."""
    tool = ev.get("tool") or ""
    if not tool or tool == "tick" or ev.get("outcome") == "call-only":
        return None
    c = (ev.get("args") or {}).get("command")
    if not isinstance(c, str):
        return tool
    c = re.sub(r"^\s*(cd\s+\S+\s*(&&|;)\s*)+", "", c)
    c = re.sub(r"^\s*(\w+=\S*\s+)+", "", c)
    w = (c.split() or [tool])[0]
    return w.split("/")[-1] or tool


def name_of(eid: str) -> str | None:
    """The act a command or tool entity stands for (c:grep -> grep, tool:read -> read)."""
    kind, _, rest = (eid or "").partition(":")
    return rest if kind in ("c", "tool") and rest else None


def count(events) -> tuple[Counter, Counter, Counter]:
    """(acts, steps out of each act, steps a -> b with b != a) over (key, ev) in seq order."""
    acts, out, steps = Counter(), Counter(), Counter()
    last: dict = {}                       # session -> (act, time)
    anon = 0
    for _k, ev in events:
        a = act_name(ev) if ev.get("stream") == "observation" else None
        if a is None:
            continue
        t = float(ev.get("ts") or (ev.get("seq") or 0) / 1e6)
        s = ev.get("session") or ""
        if not s:
            prev = last.get(f"anon{anon}")
            if prev is not None and t - prev[1] > GAP:
                anon += 1
            s = f"anon{anon}"
        acts[a] += 1
        prev = last.get(s)
        if prev is not None:
            out[prev[0]] += 1
            if prev[0] != a:
                steps[(prev[0], a)] += 1
        last[s] = (a, t)
    return acts, out, steps


def table(acts: Counter, out: Counter, steps: Counter, min_n: int = 8) -> list[dict]:
    total = sum(acts.values())
    rows = []
    for (a, b), n in steps.items():
        if n < min_n or not out[a] or not acts[b]:
            continue
        p = n / out[a]
        rows.append({"from": a, "to": b, "n": n, "share": round(p, 3),
                     "lift": round(p / (acts[b] / total), 2)})
    rows.sort(key=lambda r: (-r["lift"], -r["n"], r["from"], r["to"]))
    return rows
