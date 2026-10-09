"""THE MASSAGE (2026-10-08, the operator's design for membrane's RAG).

Not the field's state verbatim -- the field's own WORDS, picked so the
model's input leans where the field is:

  target      where the field is now: the sessions' 'now' states (the
              residual each message leaves) with the incoming message
  candidates  the field's sentences nearest the target (the sentence ANN)
  pick        greedy, under a token budget: each step takes the sentence
              that most raises cos(target, sum of the picked) per token;
              a near-copy adds ~nothing, so the set stays varied
  render      the sentences alone -- no ids, no counts, no headers beyond
              one line saying it's context
  ruler       the alignment cos(target, picked) rides out (the A/B's
              measure against the old banner)
"""
from __future__ import annotations

import re

import numpy as np

CANDIDATES = 120
BUDGET_TOKENS = 320
NOW_WEIGHT = 0.5             # the field's state vs the incoming message
MIN_GAIN = 0.003
HEAD = "[membrane] from the field, closest to where we are (context, not instructions):"


def _unit(v):
    v = np.asarray(v, dtype=np.float64)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else None


def _vec(x):
    if isinstance(x, (str, bytes)):
        from .efstore import unpack_f16
        x = unpack_f16(x)
    return np.asarray(x, dtype=np.float64)


def _tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def field_now(fld):
    """The field's present: the mean of the sessions' 'now' states."""
    nows = [v for v in (getattr(fld, "_now", None) or {}).values() if v is not None]
    return _unit(np.sum([_unit(v) for v in nows], axis=0)) if nows else None


def target(fld, query_vec):
    q = _unit(query_vec) if query_vec is not None else None
    now = field_now(fld)
    if q is None:
        return now
    if now is None:
        return q
    return _unit(NOW_WEIGHT * now + (1 - NOW_WEIGHT) * q)


def candidates(fld, t, k: int = CANDIDATES, exclude_texts=()):
    hits = fld.b.vector_top_k("sentences", "vec", list(t), k)
    docs = fld.b.get_multi("sentences", [sid for sid, _ in hits])
    ban = [_norm(x) for x in exclude_texts or () if x]
    out, seen = [], set()
    for sid, _ in hits:
        d = docs.get(sid)
        if not d or not d.get("text") or d.get("vec") is None:
            continue
        txt = " ".join(str(d["text"]).split())
        n = _norm(txt)
        if len(n) < 12 or n in seen or any(n in b for b in ban):
            continue
        v = _unit(_vec(d["vec"]))
        if v is None:
            continue
        seen.add(n)
        out.append((txt, v))
    return out


def select(t, cands, budget_tokens: int = BUDGET_TOKENS):
    picked, total, used = [], None, 0
    pool = list(cands)
    cur = 0.0
    while pool:
        best, best_score, best_new = None, MIN_GAIN, None
        for i, (txt, v) in enumerate(pool):
            cost = _tokens(txt)
            if used + cost > budget_tokens:
                continue
            s = v if total is None else total + v
            new = float(_unit(s) @ t)
            score = (new - cur) / np.sqrt(cost)
            if score > best_score:
                best, best_score, best_new = i, score, new
        if best is None:
            break
        txt, v = pool.pop(best)
        picked.append(txt)
        total = v if total is None else total + v
        used += _tokens(txt)
        cur = best_new
    return picked, (round(cur, 4) if picked else 0.0), used


def build(fld, query_vec=None, budget_tokens: int = BUDGET_TOKENS, exclude_texts=()) -> dict:
    t = target(fld, query_vec)
    if t is None:
        return {"block": "", "picked": [], "alignment": 0.0, "tokens": 0, "candidates": 0}
    cands = candidates(fld, t, exclude_texts=exclude_texts)
    picked, align, used = select(t, cands, budget_tokens)
    block = (HEAD + "\n" + "\n".join(f"- {p}" for p in picked)) if picked else ""
    return {"block": block, "picked": picked, "alignment": align,
            "tokens": used, "candidates": len(cands)}
