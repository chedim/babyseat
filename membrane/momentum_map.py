"""THROWAWAY EXPERIMENT (operator 2026-11-11): MEMBRANE_EXP_MOMENTUM_MAP=1 -- the turn's context is the doctrine,
the tools, ONE map of what is moving in the field, and the person's current message. No chat history, no banners.

A line: `<eid>  ↗ <speed>  <w1> · <w2> · <w3>`.
- speed: how far the entity's state has moved since its last closed worldline episode -- |state - centroid of the
  newest closed episode| (an entity with no closed episode: |state - seed|, its whole life so far).
- heading: the 3 word entities (kind w, live) whose states are nearest (cosine) to that move's direction
  (state - earlier, normalized). No LLM, no embedder call: the field's own states, in memory.
Only entities touched recently are candidates (the RECENT most-recently-touched); words themselves are not lines
(they flood; they are the heading vocabulary). Ranked by speed, at most MAX_LINES."""
from __future__ import annotations

import os
import sys
import time

import numpy as np

MAX_LINES = 30
RECENT = 300               # candidates: the most recently touched non-word entities
HEADER = ("What is moving (id · speed · heading). No chat history is sent. "
          "Read any id with entity read for its full story.")
_WORDS = {"t": 0.0, "M": None, "names": []}


def on() -> bool:
    return os.environ.get("MEMBRANE_EXP_MOMENTUM_MAP", "") == "1"


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def _word_matrix(states):
    if _WORDS["M"] is not None and time.time() - _WORDS["t"] < 120:
        return _WORDS["M"], _WORDS["names"]
    names, rows = [], []
    for eid, st in states:
        if st.kind == "w" and st.tier == "live" and st.state is not None:
            u = _unit(np.asarray(st.state, dtype=np.float32))
            if u is not None:
                names.append(st.surface or eid.split(":", 1)[-1])
                rows.append(u)
    M = np.vstack(rows) if rows else None
    _WORDS.update(t=time.time(), M=M, names=names)
    return M, names


def lines(fld, max_lines: int = MAX_LINES) -> list[str]:
    states = list(getattr(fld, "_states", {}).items())
    M, names = _word_matrix(states)
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
    for sp, eid, d in rows[:max_lines]:
        words = []
        u = _unit(d)
        if M is not None and u is not None:
            sims = M @ u
            words = [names[i] for i in np.argsort(-sims)[:3]]
        out.append(f"{eid}  ↗ {sp:.2f}  " + " · ".join(words))
    return out


def block(fld) -> str:
    ls = lines(fld)
    text = HEADER + "\n" + ("\n".join(ls) if ls else "(nothing moved)")
    print(f"[exp-map] {len(ls)} lines, ~{len(text) // 4} tokens", file=sys.stderr)
    return text


def rewrite(messages: list, fld) -> list:
    """The first system message (doctrine, card) + the map + the current turn (the last user message and the
    tool rounds after it). Everything else -- history, banners -- goes."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    if last_user is None:
        return messages
    head = [m for m in msgs[:last_user] if m.get("role") == "system"][:1]
    out = head + [{"role": "system", "content": block(fld)}] + msgs[last_user:]
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
