"""Shared fakes for the entity-field tests.

FakeEmbedder: deterministic across processes (sha256-seeded -- never
Python's salted hash(), the flaky-test lesson) and additive like mxbai
(a text embeds as the normalized sum of its word vectors), so sentences
sharing words are similar and a bare term embeds near its sentences."""

from __future__ import annotations

import hashlib
import re

import numpy as np

_WORD = re.compile(r"[a-z0-9_./-]+")


def word_vec(word: str, dim: int = 64) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
    v = np.random.default_rng(seed).standard_normal(dim)
    return v / np.linalg.norm(v)


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.calls = 0

    def embed(self, texts):
        out = []
        for t in texts:
            self.calls += 1
            words = _WORD.findall((t or "").lower()) or ["<empty>"]
            v = np.sum([word_vec(w, self.dim) for w in words], axis=0)
            out.append(v / (np.linalg.norm(v) + 1e-12))
        return out
