"""THE FIDELITY SEARCH (a prototype): rewrite a text so that the
embedder, reading it, lands back on a TARGET vector -- the field's own
vector for the memory the text renders.

Text is a loose rendering of a vector; a recalled memory reaches the
model as text, and the model rebuilds its meaning from that text alone.
This search closes the loop with the one reader we can query: the
embedder. An edit is kept only if it moves embed(text) toward the
target; the operators are a menu, not a grammar -- the data decides
which ones the embedder actually reads:

  slash   w -> "w/s"            the Board's group
  or      w -> "w or s"
  sub     w -> s                plain substitution
  and     + ", and t"           a region word the text lacks
  paren   + " (t)"              the same, parenthesized
  not     + ", not u"           a confuser word (does "not" push away?)
  del     - w                   drop a word that pulls elsewhere

Candidate words come from the FIELD, never a model call: synonyms from
the field's vocabulary (SlashVocab), region words from the target's
stored neighbours, confusers from where the current text lands instead.

Overfitting guard: the caller should judge the result under a HELD-OUT
embedder (experiments/e16_fidelity.py) -- a gain only the search's own
embedder sees is an adversarial artifact, not a better rendering."""

from __future__ import annotations

import re
from collections import Counter

import numpy as np

from .slash import SlashVocab, _content
from .token_timeline import tokenize

OPS = ("slash", "or", "sub", "and", "paren", "not", "del")


def _unit(M) -> np.ndarray:
    M = np.asarray(M, dtype=float)
    return M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-12)


def _replace_word(text: str, w: str, rep: str) -> str:
    return re.sub(r"\b" + re.escape(w) + r"\b", rep, text, count=1,
                  flags=re.IGNORECASE)


def region_words(texts: list[str], exclude: set[str], n: int = 12
                 ) -> list[str]:
    """The content words a set of neighbour texts share, most common
    first (document frequency -- a word every neighbour uses is the
    region's vocabulary; a word one of them repeats is not)."""
    df = Counter()
    for t in texts:
        df.update({w for w in tokenize(t) if _content(w)})
    return [w for w, _ in df.most_common() if w not in exclude][:n]


def candidates(text: str, vocab: SlashVocab, region: list[str],
               confusers: list[str], ops=OPS, k: int = 3, tau: float = 0.55
               ) -> list[tuple[str, str]]:
    """Every one-edit neighbour of `text` as (op-label, new_text)."""
    words = [w for w in dict.fromkeys(tokenize(text)) if _content(w)]
    have = set(tokenize(text))
    out: list[tuple[str, str]] = []
    if {"slash", "or", "sub"} & set(ops):
        for w in words:
            for s in vocab.neighbors(w, k=k, tau=tau):
                if "slash" in ops:
                    out.append((f"slash {w}/{s}",
                                _replace_word(text, w, f"{w}/{s}")))
                if "or" in ops:
                    out.append((f"or {w} or {s}",
                                _replace_word(text, w, f"{w} or {s}")))
                if "sub" in ops:
                    out.append((f"sub {w}->{s}", _replace_word(text, w, s)))
    for t in region:
        if t in have:
            continue
        if "and" in ops:
            out.append((f"and {t}", f"{text}, and {t}"))
        if "paren" in ops:
            out.append((f"paren {t}", f"{text} ({t})"))
    if "not" in ops:
        for u in confusers:
            if u not in have:
                out.append((f"not {u}", f"{text}, not {u}"))
    if "del" in ops and len(words) > 3:
        for w in words:
            new = re.sub(r"\s{2,}", " ", _replace_word(text, w, "")).strip()
            out.append((f"del {w}", new))
    # round-robin across operators, so a max_cands cap samples every
    # operator instead of starving the ones generated last
    by_op: dict[str, list[tuple[str, str]]] = {}
    seen = {text}
    for lab, t in out:
        if t not in seen:
            seen.add(t)
            by_op.setdefault(lab.split(" ", 1)[0], []).append((lab, t))
    uniq, lanes = [], list(by_op.values())
    for i in range(max((len(v) for v in lanes), default=0)):
        uniq.extend(v[i] for v in lanes if i < len(v))
    return uniq


def fit(text: str, target: np.ndarray, embedder, vocab: SlashVocab,
        region: list[str], confusers: list[str] | None = None, ops=OPS,
        rounds: int = 5, min_gain: float = 0.002, max_cands: int = 80
        ) -> tuple[str, list[dict]]:
    """Greedy hill-climb: each round, embed every one-edit neighbour,
    keep the best if it gains >= min_gain cosine to `target`. Returns
    (best_text, trace) -- trace[i] = {op, cos} per accepted edit, with
    trace[0] the starting point."""
    tv = _unit(target)
    cur = text
    cur_cos = float(_unit(embedder.embed([cur])[0]) @ tv)
    trace = [{"op": "start", "cos": cur_cos, "text": cur}]
    for _ in range(rounds):
        cands = candidates(cur, vocab, region, confusers or [], ops)
        if not cands:
            break
        cands = cands[:max_cands]
        V = _unit(embedder.embed([t for _, t in cands]))
        sims = V @ tv
        i = int(np.argmax(sims))
        if sims[i] - cur_cos < min_gain:
            break
        cur, cur_cos = cands[i][1], float(sims[i])
        trace.append({"op": cands[i][0], "cos": cur_cos, "text": cur})
    return cur, trace
