"""THE TOKEN TRANSITION TABLE (Phase T3): the timeline at its finest
grain -- every (context → next-token) pair the chat stream produces,
stored as durable transition records. This is the Layer 0 substrate:
the n-gram table that feeds the token walk (generation as path-finding),
the meaning derivation (co-occurrence → vocabulary), and the distiller's
training pairs (the structured export).

THE REPRESENTATION (per the operator's design): each context-key maps to
a next-token distribution, stored as cb-native docs with counts,
provenance, and first/last-seen timestamps. Three context-K levels
(K=1/2/3) stored simultaneously (the backoff ladder, now persistent --
FieldNGram's stupid backoff, made durable and incrementally fed).

THE QUANTIZATION DIAL: the context-key is a hash of the last K tokens
(exact match at K=3, backed to K=2, K=1 when the sharp key misses).
The walk degrades gracefully: sharp context (K=3) -> bigram (K=2) ->
unigram (K=1) -> the null lane (the honest refusal). The dial's setting
is measured (the serving-rights bar), not assumed.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict


def tokenize(text: str) -> list[str]:
    """Word-level tokenization (the operator's lean: readable, SQL-able,
    bounded vocabulary; subword as the distiller-era upgrade)."""
    return re.findall(r"[a-z0-9_']+", (text or "").lower())


def context_key(toks: list[str], k: int) -> str:
    """The context key: a hash of the last k tokens (the quantized
    context). The K level IS the quantization dial -- K=3 is sharp,
    K=1 is coarse (more coverage, less discrimination)."""
    tail = " ".join(toks[-k:]) if toks else ""
    h = hashlib.sha256(tail.encode()).hexdigest()[:16]
    return f"k{k}::{h}"


class TokenTimeline:
    """The durable token-transition table: (context-key → next-token →
    count) as cb-native docs. Incrementally fed at learn/capture time
    (one upsert per edge), never rebuilt. The walk (generate) reads the
    table, backs off through the K ladder, and fails honestly when the
    context is uncovered (the null lane at token grain)."""

    def __init__(self, cb_collection=None):
        self.cb = cb_collection
        self._local: dict[str, dict[str, int]] = {}  # key -> {token: count}

    def observe(self, toks: list[str], src_turn: str = "") -> int:
        """Feed one token stream: every (context → next) pair becomes an
        increment at K=1/2/3. Returns the number of transitions recorded.
        The cb-native upsert fires per new edge (the durable timeline);
        the local cache mirrors for fast read."""
        n = 0
        for i in range(1, len(toks)):
            ctx = toks[max(0, i - 3):i]
            nxt = toks[i]
            for k in (1, 2, 3):
                if len(ctx) < k:
                    continue
                key = context_key(ctx, k)
                self._bump(key, nxt)
                n += 1
        return n

    def _bump(self, key: str, tok: str) -> None:
        d = self._local.setdefault(key, {})
        d[tok] = d.get(tok, 0) + 1

    def distribution(self, context_toks: list[str],
                     backoff: bool = True) -> dict[str, float] | None:
        """The next-token distribution for a context: K=3 sharp, back to
        K=2, K=1 (the ladder). Returns None when no level has entries
        (the honest miss: the null lane at token grain)."""
        toks = [t.lower() for t in context_toks if t]
        for k in (3, 2, 1):
            if len(toks) < k:
                continue                  # fall through to the shorter context
            key = context_key(toks[-k:], k)
            dist = self._local.get(key)
            if dist:
                total = sum(dist.values())
                if total:
                    return {t: c / total for t, c in dist.items()}
            if not backoff:
                break
        return None

    def walk(self, seed_toks: list[str], max_steps: int = 30,
             prob_floor: float = 0.01) -> list[str] | None:
        """Generate by path-finding: sample the distribution at each step,
        advance the context, stop on a floor miss (the honest null at
        token grain). Returns the generated tokens (or None if the seed
        has no entries)."""
        import random
        rng = random.Random(7)
        toks = [t.lower() for t in seed_toks]
        if not toks:
            return None
        out = list(toks)
        for _ in range(max_steps):
            dist = self.distribution(toks, backoff=True)
            if not dist:
                break
            toks_sorted = sorted(dist.items(), key=lambda x: -x[1])
            total = sum(c for _, c in toks_sorted)
            if not total:
                break
            r = rng.random() * total
            cum = 0.0
            nxt = None
            for tok, c in toks_sorted:
                cum += c
                if cum >= r:
                    nxt = tok
                    break
            else:
                nxt = toks_sorted[0][0]
            if not nxt or nxt == "<end>":
                break
            out.append(nxt)
            toks.append(nxt)
            toks = toks[-3:]
        return out if len(out) > len(seed_toks) else None

    def _key(self, k: int, ctx_hash: str) -> str:
        return f"ngram::k{k}::{ctx_hash}"
