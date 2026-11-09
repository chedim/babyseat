"""#980 (R-rows): recall's POLICY as field data + methods; the native code calls it and falls back.

The policy (lane choice, rank fusion, which weight set scores a pool, when to rerank, how many banner rows,
the dedupe cut, the banner's render) is the entity `membrane::recall:dials` -- installed by the versioned seed
pack PACK (a release wins; a seat's edit is kept as an overlay, seedpack.py) -- and the methods below, which are
also registered on the `recall` class in the field (METHODS: name -> function). Native primitives stay native:
vector search, BM25, the reranker runtime (R2, R3, R8, R21).

The native callers (entity_block.rrf, recall_banner.rank / pick / render) try this module and run their old
path when it raises: `_rrf_native`, `_rank_native`, `_pick_native`, `_render_native` are that old path, and
tests/test_recall_policy_980.py holds the two to identical output on recorded queries.
"""
from __future__ import annotations

from .entities import MEMBRANE_NS

DIALS_EID = MEMBRANE_NS + "recall:dials"
_STATE: dict = {"est": None}


def _defaults() -> dict:
    """The version-1 policy: the literals recall_banner / entity_block / recall_rerank carry (they stay as the
    fallback and as the parity oracle)."""
    from . import entity_block as EB
    from . import recall_banner as RB
    from . import recall_rerank as RR
    return {
        "name": "recall dials", "version": 1, "silent_birth": True,
        "banner_n": RB.BANNER_N, "pool": RB.POOL, "no_topic_min": RB.NO_TOPIC_MIN, "fact_chars": RB.FACT_CHARS,
        "recent_turns": RB.RECENT_TURNS, "history_turns": RB.HISTORY_TURNS, "dedupe_jaccard": RB.DEDUPE_JACCARD,
        "rrf_k": EB.RRF_K, "entity_pool_max": EB.ENTITY_POOL_MAX, "rerank_top": RR.RERANK_TOP,
        "rerank": True,
        "reply_keys": list(RB.REPLY_KEYS),
        "header": RB.HEADER,
        "weights": {"default": dict(RB.DEFAULT_WEIGHTS), "legacy": dict(RB.LEGACY_WEIGHTS),
                    "rerank": dict(RB.RERANK_WEIGHTS), "legacy_rerank": dict(RB.LEGACY_RERANK_WEIGHTS),
                    "fit": "#942 (2026-11-08), LoCoMo conversations 2-9"},
        # lane choice: which lanes run for an ask (R5): the vector lanes need the ask's vector, the tool lane
        # only answers question-like asks, the word and entity lanes need no vector
        "lane_rules": {"vector_lanes": ["ask", "direct", "tool"], "tool_needs_question": True,
                       "no_vector_lanes": ["word", "entity"]},
    }


def _pack() -> dict:
    return {"name": "recall-policy", "version": 1,
            "entities": [{"eid": DIALS_EID, "cls": "Other", "properties": _defaults()}]}


class _LazyPack(dict):
    pass


def __getattr__(name):                                  # PACK is built on first use (the constants' modules import us)
    if name == "PACK":
        return _pack()
    raise AttributeError(name)


def install(est) -> None:
    """Point the policy at a seat's entity store (boot, after seedpack.load); None returns to the packaged data."""
    _STATE["est"] = est


def reset() -> None:
    _STATE["est"] = None


def dials() -> dict:
    est = _STATE["est"]
    d = (getattr(est, "docs", None) or {}).get(DIALS_EID) if est is not None else None
    return d.properties if d is not None else _defaults()


# ---- the methods (R1, R5, R6, R7, R9, R14-R20 policy parts) ---------------------------------------------

def lanes(question: bool, has_vec: bool) -> dict:
    """Lane choice: which lanes run."""
    r = dials()["lane_rules"]
    vec = bool(has_vec)
    out = {n: vec for n in r["vector_lanes"]}
    if "tool" in out:
        out["tool"] = vec and (bool(question) or not r.get("tool_needs_question", True))
    for n in r["no_vector_lanes"]:
        out[n] = True
    return {k: out[k] for k in ("ask", "direct", "tool", "word", "entity") if k in out}


def fuse(lists, key, k: int | None = None) -> list:
    """Reciprocal-rank fusion (R6): each row scores sum(1 / (rrf_k + rank)) over the lists it is in."""
    k = dials()["rrf_k"] if k is None else k
    score: dict = {}
    rep: dict = {}
    for rows in lists:
        for rank, r in enumerate(rows, 1):
            kk = key(r)
            rep.setdefault(kk, r)
            score[kk] = score.get(kk, 0.0) + 1.0 / (k + rank)
    return sorted(((rep[kk], s) for kk, s in score.items()), key=lambda t: -t[1])


def rank_policy(rows: list, weights: dict | None) -> tuple:
    """Which weight sets score this pool (R14/R18): (scorer weights, reranker weights). Rows without the reply
    models' scores, scored by a set fitted with them, go to the pre-#942 pair."""
    d = dials()
    w = d["weights"]
    keys = d["reply_keys"]
    scored_by_replies = any(any(k in r for k in keys) for r in rows)
    fitted_with_replies = all(k in (weights or w["default"]) for k in keys)
    if not scored_by_replies and fitted_with_replies:
        return dict(w["legacy"]), dict(w["legacy_rerank"])
    return (weights or dict(w["default"])), dict(w["rerank"])


def rerank_top(rerank: bool) -> int:
    """How many of the scorer's top rows the reranker re-orders (0 = none)."""
    d = dials()
    return int(d["rerank_top"]) if rerank and d.get("rerank", True) else 0


def select(ranked: list, text: str, n, skip, fact_line, sig, alike) -> list:
    """Row selection (R15/R16): ranked, the chat's own recent turns skipped, near-duplicate fact lines
    collapsed, at most n (None = the banner_n dial)."""
    d = dials()
    n = d["banner_n"] if n is None else n
    skip = set(skip)
    out, seen = [], []
    for r in ranked:
        if skip & (set(r.get("a_n") or []) | ({r["q_n"]} if r.get("q_n") is not None else set())):
            continue
        fact = fact_line(r, text)
        s = sig(fact)
        if any(alike(s, t) for t in seen):
            continue
        seen.append(s)
        out.append(dict(r, fact=fact))
        if len(out) >= n:
            break
    return out


def render(lines: list) -> str | None:
    """The banner text (R20): the header dial, then the lines."""
    return "\n".join([dials()["header"], *lines]) if lines else None


METHODS = {"lanes": lanes, "fuse": fuse, "rank_policy": rank_policy, "select": select, "render": render,
           "rerank_top": rerank_top}


def call(name: str, *a, **k):
    """The native side's single door: the field method `name` on the recall class."""
    return METHODS[name](*a, **k)
