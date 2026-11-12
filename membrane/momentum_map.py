"""THROWAWAY EXPERIMENT (operator 2026-11-11): MEMBRANE_EXP_MOMENTUM_MAP=1 -- the turn's context is the doctrine,
the tools, ONE map of what is moving in the field, and the person's current message. No chat history, no banners.

A line: `<id>  ↗ <speed>  <w1> · <w2> · <w3>`.
- id: the spec `entity read` resolves (a field mirror `m:goal:…` prints as `membrane::goal:…`; a line whose id
  resolves to nothing is left out).
- speed: how far the entity's state has moved since its last closed worldline episode -- |state - centroid of the
  newest closed episode| (an entity with no closed episode: |state - seed|, its whole life so far).
- heading: the 3 words most in the move's direction, weighted by how rare they are:
  score = cos(word state, state - earlier) × rarity. No LLM, no embedder call: the field's own states, in memory.
Candidates: every live entity except words (people, files, goals, topics, sessions...) among the RECENT most
recently touched, plus the ones this chat moved or holds; words are the heading vocabulary, not lines.

#1105 (relevance before speed; no stop-list -- nothing is junk in language, a misfiled word is a classification gap):
- ROWS: a row this chat's TURNS moved (the move log: the person's turns and the model's replies, never tool output)
  or that the context stack holds (#1101: entered, touched this turn) ranks above anything else; speed orders
  within each group. Machinery that only tool output moved is not banned -- it ranks below.
- HEADINGS: the vocabulary is the words this chat's turns moved (its last CONV_TURNS), rarity = log((T+1)/turns
  that said it) within those T turns. No conversation known: the whole field's words, rarity log(N / mentions).
- WORD KIND: identifier-shaped words (snake_case, mcp__…, ENV_NAMES, paths, file.ext, a::b, camelCase) are their
  own kind (word_kind). Headings prefer prose; identifiers compete only in a CODE chat (identifiers are at least
  CODE_SHARE of its words' mentions), else they only fill slots prose could not."""
from __future__ import annotations

import math
import os
import re
import sys
import time

import numpy as np

MAX_LINES = 30
RECENT = 2000
CONV_TURNS = 50              # the chat's latest turns read for its things and its vocabulary
CODE_SHARE = 0.1             # identifiers' share of a chat's word mentions that makes it a code chat
HEAD_FLOOR = 0.25            # a heading word scores at least this share of the best one's score
_IDENT = re.compile(r"^mcp__|[A-Za-z0-9]_[A-Za-z0-9]|::|[A-Za-z0-9.~]/[A-Za-z0-9._]|[a-z][A-Z]"
                    r"|[A-Za-z0-9]\.[A-Za-z][A-Za-z0-9]{0,4}$|^[A-Z0-9]+_[A-Z0-9_]*$")
HEADER = ("What is moving (id · speed · heading). No chat history is sent. "
          "Read any id with entity read for its full story.")
_WORDS = {"t": 0.0, "M": None, "names": [], "idf": None}


def on() -> bool:
    return os.environ.get("MEMBRANE_EXP_MOMENTUM_MAP", "") == "1"


def _unit(v):
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else None


def word_kind(name: str) -> str:
    """'identifier' for an identifier-shaped token (snake_case, mcp__…, ENV_NAME, a path, file.ext, a::b,
    camelCase), else 'prose'. A kind, not a ban: headings prefer prose outside a code chat."""
    return "identifier" if _IDENT.search(str(name or "")) else "prose"


def _word_name(eid: str, st) -> str | None:
    """A word's heading text, or None for a token that is no word (too short, no letters)."""
    name = getattr(st, "surface", "") or eid.split(":", 1)[-1]
    if len(name) < 3 or not any(c.isalpha() for c in name):
        return None
    return name


def _word_matrix(states):
    """The whole field's words, rarity log(N / mentions) -- the vocabulary when no conversation is known."""
    if _WORDS["M"] is not None and time.time() - _WORDS["t"] < 120:
        return _WORDS["M"], _WORDS["names"], _WORDS["idf"]
    names, rows, ns = [], [], []
    for eid, st in states:
        if st.kind == "w" and st.tier == "live" and st.state is not None:
            name = _word_name(eid, st)
            if name is None:
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


def _key(eid: str) -> str:
    """One spelling for a field eid and a store spec: m:goal:x / membrane::goal:x -> goal:x."""
    e = str(eid or "")
    if e.startswith("membrane::"):
        e = e[len("membrane::"):]
    return e[2:] if e.startswith("m:") else e


def conversation(fld, chat: str = ""):
    """What this chat's turns moved (the move log; tool output is never a turn): ({thing eid: turns},
    {word eid: turns}, T). chat '' = the chat with the latest turn; an unknown chat moved nothing."""
    ml = getattr(fld, "move_log", None)
    turns_by_chat = getattr(ml, "_turns", None) or {}
    if not chat and turns_by_chat:
        chat = max(turns_by_chat, key=lambda c: turns_by_chat[c][-1][0] if turns_by_chat[c] else -1)
    turns = list(turns_by_chat.get(chat) or [])[-CONV_TURNS:]
    things, words = {}, {}
    moves = getattr(ml, "moves", None) or {}
    for _seq, key in turns:
        for eid in (moves.get(key) or {}):
            book = words if str(eid).startswith("w:") else things
            book[eid] = book.get(eid, 0) + 1
    return things, words, len(turns)


def _conv_vocab(states: dict, words: dict, T: int):
    """The conversation's own vocabulary: (M, names, rarity within its T turns, code chat?) or None."""
    names, rows, rar, ident, total = [], [], [], 0, 0
    for eid, df in words.items():
        st = states.get(eid)
        if st is None or st.state is None:
            continue
        name = _word_name(eid, st)
        u = _unit(np.asarray(st.state, dtype=np.float32)) if name else None
        if u is None:
            continue
        names.append(name)
        rows.append(u)
        rar.append(math.log((T + 1) / df))
        total += df
        ident += df if word_kind(name) == "identifier" else 0
    if not rows:
        return None
    return np.vstack(rows), names, np.array(rar, dtype=np.float32), ident >= CODE_SHARE * total


def _heading(M, names, idf, u, code: bool, k: int = 3) -> list:
    """The k best words by score; outside a code chat prose first, identifiers only fill what prose could not."""
    score = (M @ u) * idf
    idx = np.argsort(-score)
    best = float(score[idx[0]]) if len(idx) else 0.0
    if best > 0:                                     # a heading carries weight: no filler from a thin vocabulary
        idx = [i for i in idx if float(score[i]) >= HEAD_FLOOR * best]
    order = [names[i] for i in idx]
    if code:
        return order[:k]
    prose = [w for w in order if word_kind(w) == "prose"]
    return (prose + [w for w in order if word_kind(w) != "prose"])[:k]


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


def lines(fld, est=None, max_lines: int = MAX_LINES, chat: str = "", held=()) -> list[str]:
    smap = dict(getattr(fld, "_states", {}) or {})
    states = list(smap.items())
    things, words, T = conversation(fld, chat)
    vocab = _conv_vocab(smap, words, T)
    if vocab is not None:
        M, names, idf, code = vocab
    else:
        M, names, idf = _word_matrix(states)
        code = False
    relevant = {_key(e) for e in things} | {_key(e) for e in (held or ())}
    cands = [(st.last_ts or 0.0, eid, st) for eid, st in states
             if st.kind != "w" and st.tier == "live" and st.state is not None]
    cands.sort(key=lambda r: -r[0])
    pool = cands[:RECENT] + [c for c in cands[RECENT:] if _key(c[1]) in relevant]
    rows = []
    for _ts, eid, st in pool:
        cur = np.asarray(st.state, dtype=np.float32)
        prev = (np.asarray(st.episodes[-1][0], dtype=np.float32) if st.episodes
                else (np.asarray(st.seed, dtype=np.float32) if st.seed is not None else None))
        if prev is None:
            continue
        d = cur - prev
        sp = float(np.linalg.norm(d))
        if sp < 1e-6:
            continue
        rows.append((_key(eid) not in relevant, sp, eid, d))
    rows.sort(key=lambda r: (r[0], -r[1]))           # relevance first, then speed
    out = []
    for _far, sp, eid, d in rows:
        if len(out) >= max_lines:
            break
        spec = _readable(est, eid)
        if spec is None:
            continue
        words = []
        u = _unit(d)
        if M is not None and u is not None:
            words = _heading(M, names, idf, u, code)
        out.append(f"{spec}  ↗ {sp:.2f}  " + " · ".join(words))
    return out


def block(fld, est=None, ctx_line: str = "", chat: str = "", held=(), banner: str = "") -> str:
    """The block the model gets IS the banner (operator 2026-11-11): its instance's `text`, built by the banner pack
    from the facts the seat wrote (`lines` below are the map's rows). Nothing is added around it here."""
    return banner


def rewrite(messages: list, fld, est=None, ctx_line: str = "", chat: str = "", held=(), banner: str = "") -> list:
    """The first system message (doctrine, card) + the map + the current turn (the last user message and the
    tool rounds after it). Everything else -- history, banners -- goes."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=None)
    if last_user is None:
        return messages
    head = [m for m in msgs[:last_user] if m.get("role") == "system"][:1]
    out = head + [{"role": "system", "content": block(fld, est, ctx_line, chat, held, banner)}] + msgs[last_user:]
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
