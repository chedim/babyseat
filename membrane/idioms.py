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


# ---- THE SUGGESTED METHODS: an idiom offered as a cliffhanger method ----------------
_SEP = re.compile(r"\s(?:\|\|?|&&|;)\s|;")


def first_command(cmd: str) -> str:
    """The command an act is (cd/env prefixes dropped, the pipe's tail cut)."""
    c = re.sub(r"^\s*(cd\s+\S+\s*(&&|;)\s*)+", "", cmd or "")
    c = re.sub(r"^\s*(\w+=\S*\s+)+", "", c)
    return _SEP.split(c, 1)[0].strip()


_REDIRECT = re.compile(r"^(\d*|&)?[<>]")


def _tokens(cmd: str) -> list[str]:
    """The command's words, redirections dropped (2>/dev/null is plumbing,
    never an argument -- on a real field it read as a 'shared path')."""
    import shlex
    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    out, skip = [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(t):
            skip = t in (">", ">>", "<", "1>", "2>", "2>>", "&>")   # a bare '> file': the file goes too
            continue
        out.append(t)
    return out


def pairs(events) -> dict:
    """(act a, act b) -> [(command a, command b)]: consecutive command acts of a
    session, a != b (the same sessions count() reads)."""
    out: dict = {}
    last: dict = {}
    anon = 0
    for _k, ev in events:
        a = act_name(ev) if ev.get("stream") == "observation" else None
        if a is None:
            continue
        t = float(ev.get("ts") or (ev.get("seq") or 0) / 1e6)
        s = ev.get("session") or ""
        if not s:
            prev = last.get(f"anon{anon}")
            if prev is not None and t - prev[2] > GAP:
                anon += 1
            s = f"anon{anon}"
        c = (ev.get("args") or {}).get("command")
        prev = last.get(s)
        if prev is not None and prev[0] != a and isinstance(c, str) and isinstance(prev[1], str):
            out.setdefault((prev[0], a), []).append((prev[1], c))
        last[s] = (a, c, t)
    return out


def _param(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_") or "x"


def generalize(a: str, b: str, ca: str, cb: str):
    """Two commands of an idiom as a two-step template: what both name becomes
    {{path}} (a path) or {{target}}, every other argument its step's own numbered
    param, flags and the program stay as written. None when they share nothing."""
    import shlex
    ta, tb = _tokens(first_command(ca)), _tokens(first_command(cb))
    if len(ta) < 2 or len(tb) < 2:
        return None
    shared = list(dict.fromkeys(t for t in ta[1:] if not t.startswith("-") and len(t) > 2 and t in tb[1:]))
    if not shared:
        return None
    names: dict = {}
    for t in shared:
        base = "path" if ("/" in t or re.search(r"\.\w{1,5}$", t)) else "target"
        n = sum(1 for v in names.values() if v.startswith(base))
        names[t] = base if n == 0 else f"{base}{n + 1}"

    def gen(toks, step):
        out, k = [shlex.quote(toks[0])], 0
        for t in toks[1:]:
            if t in names:
                out.append("{{" + names[t] + "}}")
            elif t.startswith("-"):
                out.append(shlex.quote(t))
            else:
                k += 1
                out.append("{{" + f"{_param(step)}_{k}" + "}}")
        return " ".join(out)
    return gen(ta, a), gen(tb, b)


def suggest(instances: dict, idioms: dict, min_n: int = 3) -> list[dict]:
    """The idioms' most common two-step shapes, offered as exec(action='define')
    composites -- never defined here: defining stays a deliberate act."""
    from collections import Counter
    out = []
    for key, row in idioms.items():
        a, b = key
        inst = instances.get(key) or []
        shapes: Counter = Counter()
        example: dict = {}
        for ca, cb in inst:
            g = generalize(a, b, ca, cb)
            if g is not None:
                shapes[g] += 1
                example.setdefault(g, (ca, cb))
        if not shapes:
            continue
        (ga, gb), n = shapes.most_common(1)[0]
        if n < min_n:
            continue
        params = list(dict.fromkeys(re.findall(r"\{\{(\w+)\}\}", ga + " " + gb)))
        method = f"{_param(a)}_then_{_param(b)}"
        out.append({"from": a, "to": b, "n": n, "of": len(inst), "lift": row.get("lift"),
                    "method": method, "params": params,
                    "define": {"action": "define", "on": "", "method": method,
                               "steps": [{"name": _param(a), "command": ga}, {"name": _param(b), "command": gb}]},
                    "example": list(example[(ga, gb)])})
    out.sort(key=lambda s: (-s["n"], -(s["lift"] or 0), s["from"], s["to"]))
    return out
