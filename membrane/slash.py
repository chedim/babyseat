"""THE BOARD'S DIALECT (the slash lane, a prototype): a query word the
field has barely seen is rewritten as a slash-group of the field's OWN
nearest words -- "undo" -> "undo/revert/rollback" -- and the query is
re-embedded as one string.

Why a slash-group, not an average: the embedder encodes the group
JOINTLY, so attention reads each term in the light of the others -- the
group lands on what the terms SHARE (the Board's "Director/Guardian" is
the one concept both words name), not on the centroid of their senses.

Why only the rare words: the recall miss this targets is the LEXICAL
one -- a paraphrase that names the situation with words the field never
used. Words the field already speaks stay as they are (expanding them
only drifts the query). The synonyms come from the field's vocabulary,
never a model call: the translation is INTO the field's dialect.

The lane is advisory: the caller keeps the plain embedding and merges
(max per exemplar), so a wrong synonym can only add a candidate, never
take the plain hit away. experiments/e15_slash_recall.py measures it."""

from __future__ import annotations

import re
from collections import Counter

import numpy as np

from .token_timeline import tokenize

STOP = frozenset("""
a an the and or but if then else of to in on at by for with from into onto
is are was were be been being am do does did done have has had having it its
this that these those there here what which who whom whose when where why how
i me my we our you your he she they them their his her us not no yes so than
too very can could should would will shall may might must just also only all
any some each every both either neither more most less least such own same
other another about above below over under again further once up down out off
like get got let lets make made please ok okay yeah hey hi oh lol one two
""".split())

SLASH_MIN_FREQ = 3        # a word seen < this often in the field is "foreign"
SLASH_TAU = 0.72          # min token-token cosine for a synonym to join
SLASH_K = 2               # synonyms per foreign word (the group is k+1 wide)
VOCAB_MIN_FREQ = 3        # the field's dialect: words it used this often
VOCAB_MAX = 5000          # cap (most frequent first) -- one embed each, once


def _content(tok: str) -> bool:
    return len(tok) >= 3 and tok.isalpha() and tok not in STOP


def _same_stem(a: str, b: str) -> bool:
    """test/tests/testing is inflection, not a synonym -- it adds no
    vocabulary the embedder does not already bridge."""
    cp = 0
    for x, y in zip(a, b):
        if x != y:
            break
        cp += 1
    short = min(len(a), len(b))
    return cp >= min(4, short) and cp >= short - 3


class SlashVocab:
    """The field's dialect: its frequent content words, each embedded
    bare once (the codebook-fill pattern), unit-normed for cosine."""

    def __init__(self, embedder, texts, min_freq: int = VOCAB_MIN_FREQ,
                 max_words: int = VOCAB_MAX):
        self.embedder = embedder
        self.freq = Counter(t for s in texts for t in tokenize(s)
                            if _content(t))
        words = [w for w, c in self.freq.most_common(max_words)
                 if c >= min_freq]
        self.words = words
        self.M = self._embed(words) if words else np.zeros((0, 1))

    def _embed(self, words: list[str]) -> np.ndarray:
        out = []
        for lo in range(0, len(words), 256):
            out.extend(self.embedder.embed(words[lo:lo + 256]))
        M = np.asarray(out, dtype=float)
        return M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)

    def neighbors(self, word: str, k: int = SLASH_K,
                  tau: float = SLASH_TAU) -> list[str]:
        """The field's nearest words to `word` (cos >= tau, no
        inflections of it, no self)."""
        if not self.words:
            return []
        v = self._embed([word])[0]
        sims = self.M @ v
        out = []
        for i in np.argsort(-sims):
            if sims[i] < tau or len(out) >= k:
                break
            w = self.words[i]
            if w == word or _same_stem(w, word) or any(
                    _same_stem(w, o) for o in out):
                continue
            out.append(w)
        return out

    def foreign(self, text: str, min_freq: int = SLASH_MIN_FREQ) -> list[str]:
        """The query's content words the field has (almost) never used."""
        seen, out = set(), []
        for t in tokenize(text):
            if _content(t) and t not in seen and self.freq[t] < min_freq:
                seen.add(t)
                out.append(t)
        return out

    def _in_context(self, text: str, word: str, syns: list[str],
                    ctx_min: float) -> list[str]:
        """THE BOARD'S TEST (the antonym guard): bare-word embeddings put
        antonyms side by side ("accessible" ~ "disabled"), so a synonym
        joins only if swapping it INTO THIS SENTENCE keeps the sentence
        where it was (cos >= ctx_min) -- the group must name the same
        thing here, not merely live nearby in the lexicon."""
        pat = re.compile(r"\b" + re.escape(word) + r"\b", re.IGNORECASE)
        swapped = [pat.sub(s, text) for s in syns]
        V = self._embed([text] + swapped)
        return [s for s, c in zip(syns, V[1:] @ V[0]) if c >= ctx_min]

    def groups(self, text: str, *, every: bool = False, k: int = SLASH_K,
               tau: float = SLASH_TAU, ctx_min: float | None = None
               ) -> dict[str, list[str]]:
        """{word: [synonyms]} for each foreign word (every content word
        with every=True) that found field-dialect synonyms. ``ctx_min``
        (optional) runs the antonym guard on each group."""
        targets = ([t for t in dict.fromkeys(tokenize(text)) if _content(t)]
                   if every else self.foreign(text))
        out = {}
        for t in targets:
            syn = self.neighbors(t, k=k, tau=tau)
            if syn and ctx_min is not None:
                syn = self._in_context(text, t, syn, ctx_min)
            if syn:
                out[t] = syn
        return out

    def expand(self, text: str, **kw) -> str:
        """The Board's rewrite: each grouped word becomes "word/syn/syn"
        IN PLACE -- the rest of the sentence is untouched, so the group
        is read in context. Keywords as ``groups``."""
        g = self.groups(text, **kw)
        if not g:
            return text
        pat = re.compile(r"\b(" + "|".join(map(re.escape, g)) + r")\b",
                         re.IGNORECASE)
        return pat.sub(lambda m: "/".join([m.group(1)] + g[m.group(1).lower()]),
                       text)

    def variants(self, text: str, **kw) -> list[str]:
        """The MULTI-QUERY control (what RAG-Fusion-style systems do):
        one sentence per single swap, each to be embedded SEPARATELY --
        the same synonyms as ``expand``, without the joint encoding."""
        out = []
        for w, syns in self.groups(text, **kw).items():
            pat = re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE)
            out.extend(pat.sub(s, text) for s in syns)
        return out


def merge_max(*score_rows: np.ndarray) -> np.ndarray:
    """The advisory merge: per exemplar, the best of the lanes -- the
    slash lane can add a candidate, never demote the plain hit."""
    return np.max(np.vstack(score_rows), axis=0)
