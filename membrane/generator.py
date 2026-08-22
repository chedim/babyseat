"""Fork C: the boundary's generative organ, grown from the field.

The live worlds LEARNED but never spoke for themselves: a gate ``act`` REPLAYED a
stored action verbatim -- a repeater, not a speaker. ``Generator`` replaces that
replay. It is a word-level n-gram LANGUAGE MODEL trained on the field's own
stored actions -- nothing downloaded, no borrowed brain. The counts ARE the
field's memory of what it has said. On ``act`` the boundary leans on the nearest
memory of the query (kNN conditioning) and GENERATES a novel response from those
statistics -- not a verbatim replay, a fresh draw from the joint distribution of
what the field has said in similar situations.

This is the hard path (Fork C): the generative organ is grown FROM the field
with nothing to hide behind, so "all output made by Cortex" is literally true.
A word-level n-gram with stupid backoff is a real, learned language model (it is
what "training a language model" meant before neural LMs) -- and it is feasible
to grow in-process with no dependencies, which a neural organ is not (yet).

Cold-start honesty: a field of a few dozen short stub actions yields a sparse,
rough n-gram. That is a FEATURE, not a bug -- it is the cell's first stammer,
the way a child's first sentences are fragments of what it has heard. As the
world actually lives (the observer forwards + learns REAL responses, which
enter the field), the action corpus grows richer and the n-gram sharpens. Train
the way you train anything: start rough, grow.

Determinism: a given query seeds the RNG stably (sha256 of query + nearest
action), so a RE-ASK of the same query generates the SAME output -- the re-ask
detector then reads "the boundary said this and the user repeated -> the
generation was wrong -> demote," exactly the existing green->red->green loop,
now aimed at a generated (not replayed) response. The self-correction arc the
repeater already had is inherited unchanged.

Planned (not yet built): a shadow judge. On ``act``, also fetch what the LLM
would say (background, non-blocking -- the user still gets the boundary's
generated output), embed both, and reuse the gate's confidence as a comparator:
cosine(generated, llm) >= sim_min -> they agree -> the generator was calibrated
(a confirmation, the existing ``contest``); cosine < sim_min -> "not sure they
agree" -> learn the LLM's output into the field the way an escalation does, so
the generator's corpus grows toward the real answer. The model is a teacher,
never the author of live output. Deferred by the user ("later"). The first
build here is pure: generate and deliver, no judge.
"""
from __future__ import annotations

import hashlib
import random
import re

# a field below this many distinct stored actions is too thin to speak from --
# the n-gram would emit near-random tokens. Fall back to the verbatim replay
# (the repeater) until the world has lived enough to have something to say.
MIN_ACTIONS = 3
_N = 3                       # trigram (2-token history)
_BACKOFF = 0.4               # stupid-backoff discount (Brants 2000)
_BOS, _EOS = "<s>", "</s>"
_TEMP = 0.7                  # sampling temperature (low = stay close to the field)
_MAX = 18                    # cap generated tokens (the stubs are short; a stammer
                            # is a fragment, not a speech)
_REPEAT_PEN = 0.35           # score multiplier on tokens already emitted this turn
                            # -- stops the trigram looping on its single hottest n-gram
                            # (the classic n-gram "gets stuck" failure)
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?|[^\sa-z0-9]+")


def _tok(text: str) -> list[str]:
    """Lowercase word/punctuation tokens. Apostrophes stay inside a word
    (``don't`` -> one token); punctuation runs are their own tokens."""
    return _TOKEN.findall((text or "").lower())


class FieldNGram:
    """A word-level n-gram language model, trained on a corpus of action strings.

    Pure Python, no dependencies: counts n-grams of order 1..``n`` and scores a
    next token via STUPID BACKOFF -- if the trigram was never seen, fall back to
    the bigram at a 0.4 discount, then the unigram, then uniform over the
    vocabulary. Stupid backoff is not smooth-probability-correct, but it is a
    well-tested, simple LM that works; it is the honest first organ. The model
    is read-only after build; rebuild it when the field grows.
    """

    def __init__(self, actions: list[str], n: int = _N):
        self.n = n
        self.counts: dict[tuple, dict[str, int]] = {}   # history -> {token: count}
        self.context: dict[tuple, int] = {}            # history -> total count
        self.vocab: list[str] = []
        seen: set[str] = set()
        for a in actions:
            toks = _tok(a)
            if not toks:
                continue
            seq = [_BOS] + toks + [_EOS]
            seen.update(toks)
            for order in range(1, n + 1):
                for i in range(len(seq) - order):
                    h = tuple(seq[i:i + order])
                    w = seq[i + order]
                    self.counts.setdefault(h, {})
                    self.counts[h][w] = self.counts[h].get(w, 0) + 1
                    self.context[h] = self.context.get(h, 0) + 1
        self.vocab = sorted(seen)

    def _score(self, hist: tuple, w: str) -> float:
        """Stupid-backoff probability of ``w`` after ``hist`` (longest match)."""
        for k in range(min(self.n, len(hist)), 0, -1):
            h = hist[-k:]
            c = self.counts.get(h)
            if c and w in c:
                tot = self.context.get(h, 0) or 1
                return c[w] / tot * (_BACKOFF ** (min(self.n, len(hist)) - k))
        # unigram fallback then uniform
        u = self.counts.get((), {})
        tot = self.context.get((), 0) or 1
        if w in u:
            return u[w] / tot * (_BACKOFF ** min(self.n, len(hist)))
        return (1.0 / max(1, len(self.vocab))) * (_BACKOFF ** (min(self.n, len(hist)) + 1))

    def generate(self, seed: list[str], rng: random.Random,
                 temp: float = _TEMP, max_tokens: int = _MAX) -> str:
        """Generate from ``seed`` (a kNN lead from the nearest action), sampling
        with temperature until ``</s>`` or ``max_tokens``. Returns a detokenized
        string (tokens joined with spaces, punctuation glued left).

        A repetition penalty (``_REPEAT_PEN``) discounts tokens already emitted
        this turn, so a sparse cold n-gram does not loop forever on its single
        hottest phrase -- the first stammer is a fragment, not a stuck tape."""
        if not self.vocab:
            return ""
        hist: list[str] = [_BOS] + seed[-2:]
        out: list[str] = []
        emitted: set[str] = set()
        for _ in range(max_tokens):
            # candidate next tokens: any vocab token (bounded) scored by the model
            scores = [(w, self._score(tuple(hist), w)) for w in self.vocab]
            if not scores:
                break
            # repetition penalty: discount tokens already emitted this turn
            scores = [(w, s * (_REPEAT_PEN if w in emitted else 1.0)) for w, s in scores]
            # temperature sampling
            if temp <= 0:
                w = max(scores, key=lambda x: x[1])[0]
            else:
                scores = [(w, s ** (1.0 / temp)) for w, s in scores]
                tot = sum(s for _, s in scores) or 1.0
                r = rng.random() * tot
                acc = 0.0
                w = scores[-1][0]
                for ww, s in scores:
                    acc += s
                    if r <= acc:
                        w = ww
                        break
            if w == _EOS:
                break
            out.append(w)
            emitted.add(w)
            hist = (hist + [w])[-self.n:]
        return _detok(out)

    @property
    def size(self) -> int:
        return len(self.vocab)


def _detok(toks: list[str]) -> str:
    """Join tokens back to text: punctuation runs glue to the left with no space
    (so ``['at', 'noon', ';']`` -> ``at noon;``), words space-separated."""
    if not toks:
        return ""
    parts: list[str] = []
    for t in toks:
        if t and not t[0].isalnum() and parts:    # punctuation -> glue left
            parts[-1] += t
        else:
            parts.append(t)
    return " ".join(parts)