"""THROWAWAY EXPERIMENT (operator 2026-11-11): MEMBRANE_EXP_MOMENTUM_MAP=1 -- the turn's context is the doctrine,
the tools, ONE map of what is moving in the field, and the person's current message. No chat history, no banners.

A line: `<id>  ↗ <speed>  <w1> · <w2> · <w3>`.
- id: the spec `entity read` resolves (a field mirror `m:goal:…` prints as `membrane::goal:…`; a line whose id
  resolves to nothing is left out).
- speed: how far the entity's state has moved since its last closed worldline episode -- |state - centroid of the
  newest closed episode| (an entity with no closed episode: |state - seed|, its whole life so far).
- heading: the 3 words most in the move's direction, weighted by how rare they are in the field:
  score = cos(word state, state - earlier) × log(N / mentions), stop words and short words skipped.
  No LLM, no embedder call: the field's own states, in memory.
Candidates: every live entity except words (people, files, goals, topics, sessions...) among the RECENT most
recently touched; words are the heading vocabulary, not lines. Ranked by speed, at most MAX_LINES."""
from __future__ import annotations

import math
import os
import sys
import time

import numpy as np

MAX_LINES = 30
RECENT = 2000
HEADER = ("What is moving (id · speed · heading). No chat history is sent. "
          "Read any id with entity read for its full story.")
_WORDS = {"t": 0.0, "M": None, "names": [], "idf": None}


def on() -> bool:
    return os.environ.get("MEMBRANE_EXP_MOMENTUM_MAP", "") == "1"


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def _stop() -> frozenset:
    try:
        from .recall_banner import _STOP
        return _STOP
    except Exception:
        return frozenset()


def _word_matrix(states):
    if _WORDS["M"] is not None and time.time() - _WORDS["t"] < 120:
        return _WORDS["M"], _WORDS["names"], _WORDS["idf"]
    stop = _stop()
    names, rows, ns = [], [], []
    for eid, st in states:
        if st.kind == "w" and st.tier == "live" and st.state is not None:
            name = st.surface or eid.split(":", 1)[-1]
            if len(name) < 3 or name.lower() in stop or not any(c.isalpha() for c in name):
                continue
            u = _unit(np.asarray(st.state, dtype=np.float32))
            if u is not None:
                names.append(name)
                rows.append(u)
                ns.append(max(1, int(getattr(st, "n", 1) or 1)))
    M = np.vstack(rows) if rows else None
    total = float(sum(ns)) or 1.0
    idf = np.array([math.log(total / n) for n in ns], dtype=np.float32) if ns else None
    _WORDS.update(t=time.time(), M=M, names=names, idf=idf)
    return M, names, idf


def _readable(est, eid: str) -> str | None:
    """The spec entity read resolves for a field id (None: it resolves to nothing)."""
    if est is None:
        return eid
    rest = eid.split(":", 1)[1] if ":" in eid else eid
    cands = [eid]
    if eid.startswith("m:"):
        cands.insert(0, "membrane::" + rest)
    cands.append("membrane::" + eid)
    for c in cands:
        try:
            if est._resolve_spec(c) is not None:
                return c
        except Exception:
            continue
    return None


def lines(fld, est=None, max_lines: int = MAX_LINES) -> list[str]:
    states = list(getattr(fld, "_states", {}).items())
    M, names, idf = _word_matrix(states)
    cands = [(st.last_ts or 0.0, eid, st) for eid, st in states
             if st.kind != "w" and st.tier == "live" and st.state is not None]
    cands.sort(key=lambda r: -r[0])
    rows = []
    for _ts, eid, st in cands[:RECENT]:
        cur = np.asarray(st.state, dtype=np.float32)
        prev = (np.asarray(st.episodes[-1][0], dtype=np.float32) if st.episodes
                else (np.asarray(st.seed, dtype=np.float32) if st.seed is not None else None))
        if prev is None:
            continue
        d = cur - prev
        sp = float(np.linalg.norm(d))
        if sp < 1e-6:
            continue
        rows.append((sp, eid, d))
    rows.sort(key=lambda r: -r[0])
    out = []
    for sp, eid, d in rows:
        if len(out) >= max_lines:
            break
        spec = _readable(est, eid)
        if spec is None:
            continue
        words = []
        u = _unit(d)
        if M is not None and u is not None:
            score = (M @ u) * idf
            words = [names[i] for i in np.argsort(-score)[:3]]
        out.append(f"{spec}  ↗ {sp:.2f}  " + " · ".join(words))
    return out


def block(fld, est=None, ctx_line: str = "") -> str:
    ls = lines(fld, est)
    text = HEADER + ("\n" + ctx_line if ctx_line else "") + "\n" + ("\n".join(ls) if ls else "(nothing moved)")
    print(f"[exp-map] {len(ls)} lines, ~{len(text) // 4} tokens", file=sys.stderr)
    return text


def rewrite(messages: list, fld, est=None, ctx_line: str = "") -> list:
    """The first system message (doctrine, card) + the map + the current turn (the last user message and the
    tool rounds after it). Everything else -- history, banners -- goes."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    if last_user is None:
        return messages
    head = [m for m in msgs[:last_user] if m.get("role") == "system"][:1]
    out = head + [{"role": "system", "content": block(fld, est, ctx_line)}] + msgs[last_user:]
    try:                                   # what the model got, for the operator to look at
        import json
        with open(os.path.join(".membrane", "exp-map-last.json"), "w") as f:
            json.dump({"t": time.time(), "dropped": len(msgs) - (len(out) - 1),
                       "messages": [{"role": m.get("role"), "chars": len(str(m.get("content") or "")),
                                     **({"content": m.get("content")} if i == len(head) else {})}
                                    for i, m in enumerate(out)]}, f, indent=1, ensure_ascii=False)
    except OSError:
        pass
    return out
