"""RECALL RENDERERS (a prototype -- experiments/e17_render_samples.py drives
it; not wired into the seat): four ways to turn the same recalled
memories into the text block the model reads.

  today       the seat's current shape (rag._memory_block): clip each
              side to N chars, MMR-drop near-duplicates, stop at the
              budget. A clip cuts wherever the counter runs out.
  spans       same slots, same size -- but each side is cut to the
              SENTENCES that best carry the memory (chosen by fidelity:
              the embedding of the kept text lands closest to the
              memory's own vector). Verbatim spans: readable by
              construction, never word salad.
  spans+tags  + a "topics:" line per memory: the memory's own
              distinctive words the cut dropped (from ITS text, never
              from neighbours -- no copying, no drift toward the cluster).
  clustered   near-duplicates are GROUPED instead of dropped: one
              representative per group + one-line gists of the rest +
              the group's counts ("4 memories agree", "asked 3x") --
              the field's geometry, written out as text.

Every renderer takes the same candidate pool and the same char budget,
so the samples compare like for like."""

from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .slash import STOP
from .token_timeline import tokenize


def _unit(M) -> np.ndarray:
    M = np.asarray(M, dtype=float)
    return M / (np.linalg.norm(M, axis=-1, keepdims=True) + 1e-12)


def clean(text: str) -> str:
    return " ".join((text or "").split())


def clip(s: str, cap: int) -> str:                 # rag._clip, verbatim
    s = clean(s)
    return s if len(s) <= cap else s[: max(1, cap - 1)] + "..."


# ---- spans: the sentence-level cut ----------------------------------------

_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|(?<=;)\s+|\s+(?=-\s)")


def spans(text: str, max_len: int = 240) -> list[str]:
    """Sentence-ish spans; an over-long span splits at commas, then hard."""
    out = []
    for s in _SPLIT.split(text or ""):
        s = clean(s)
        if not s:
            continue
        while len(s) > max_len:
            cut = s.rfind(", ", 0, max_len)
            cut = cut + 1 if cut > max_len // 3 else max_len
            out.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            out.append(s)
    return out


def select_spans(text: str, target: np.ndarray, embedder, cap: int,
                 rounds: int = 4) -> tuple[str, float]:
    """Keep the spans (original order) whose JOINT embedding lands closest
    to `target`, within `cap` chars. Greedy: best single span, then add
    the span that raises the cosine most, while it fits and gains.
    Non-adjacent spans join with ' … ' so the reader sees the gap.
    Returns (text, cosine)."""
    sp = spans(text)
    t = _unit(target)
    if not sp:
        return "", 0.0
    if len(clean(text)) <= cap:                    # it all fits: no cut
        return clean(text), float(_unit(embedder.embed([clean(text)])[0]) @ t)

    def join(idx):
        idx = sorted(idx)
        parts, prev = [], None
        for i in idx:
            if prev is not None and i != prev + 1:
                parts.append("…")
            parts.append(sp[i])
            prev = i
        return " ".join(parts)

    fits = [i for i, s in enumerate(sp) if len(s) <= cap]
    if not fits:                                   # one giant span: clip it
        c = clip(sp[0], cap)
        return c, float(_unit(embedder.embed([c])[0]) @ t)
    V = _unit(embedder.embed([sp[i] for i in fits]))
    best = fits[int(np.argmax(V @ t))]
    chosen = {best}
    cur = join(chosen)
    cur_cos = float(_unit(embedder.embed([cur])[0]) @ t)
    for _ in range(rounds):
        cands = [i for i in range(len(sp)) if i not in chosen
                 and len(join(chosen | {i})) <= cap]
        if not cands:
            break
        texts = [join(chosen | {i}) for i in cands]
        sims = _unit(embedder.embed(texts)) @ t
        j = int(np.argmax(sims))
        if sims[j] <= cur_cos + 0.002:
            break
        chosen.add(cands[j])
        cur, cur_cos = texts[j], float(sims[j])
    return cur, cur_cos


# ---- tags: the words the cut dropped -------------------------------------

def doc_freq(texts) -> tuple[Counter, int]:
    df = Counter()
    n = 0
    for t in texts:
        df.update({w for w in tokenize(t) if len(w) >= 3 and w.isalpha()})
        n += 1
    return df, n


def topic_terms(full: str, shown: str, df: Counter, n_docs: int,
                k: int = 5) -> list[str]:
    """The memory's most DISTINCTIVE words (tf-idf against the whole
    field) that the rendered text does not already show."""
    have = set(tokenize(shown))
    tf = Counter(w for w in tokenize(full)
                 if len(w) >= 3 and w.isalpha() and w not in STOP)
    score = {w: c * math.log((n_docs + 1) / (df[w] + 1))
             for w, c in tf.items() if w not in have}
    return [w for w, _ in sorted(score.items(), key=lambda x: -x[1])][:k]


# ---- the four renderers ----------------------------------------------------

def _meta(m: dict) -> str:
    """The record's own evidence, spelled out: how often it was re-asked,
    used, voted on; whether it is still pending confirmation."""
    bits = []
    if int(m.get("reask_count") or 0):
        bits.append(f"re-asked {m['reask_count']}x")
    if len(m.get("uses") or []):
        bits.append(f"used {len(m['uses'])}x")
    if (m.get("outcome") or "") not in ("", "pending"):
        bits.append(str(m["outcome"])[:24])
    if m.get("ephemeral"):
        bits.append("unconfirmed")
    return ", ".join(bits)


def render_today(pool, budget: int, mem_chars: int = 220,
                 mmr: float = 0.90, k: int = 24) -> tuple[str, list[int]]:
    """The seat's current block (rag._memory_block's shape)."""
    picked, pv, used = [], [], []
    left = budget
    for i, m in enumerate(pool):
        v = _unit(m["vec"])
        if any(float(v @ p) >= mmr for p in pv):
            continue
        q, a = clip(m["situation"], mem_chars), clip(m["action"], mem_chars)
        entry = f"situation: {q}\nresponse: {a} (sim {m['sim']:.2f})"
        if len(entry) > left:
            break
        left -= len(entry)
        picked.append(entry)
        pv.append(v)
        used.append(i)
        if len(picked) >= k:
            break
    return "Recalled memories:\n\n" + "\n----\n".join(picked), used


def _span_entry(m, embedder, mem_chars, tags, df, n_docs):
    q, qc = select_spans(m["situation"], np.asarray(m["vec"]), embedder,
                         mem_chars)
    a_full = clean(m["action"])
    a_target = embedder.embed([a_full[:2000]])[0]
    a, ac = select_spans(a_full, a_target, embedder, mem_chars)
    head = f"(sim {m['sim']:.2f}"
    meta = _meta(m)
    head += f" · {meta})" if meta else ")"
    entry = f"situation: {q}\nresponse: {a} {head}"
    if tags:
        terms = topic_terms(m["situation"] + " " + m["action"], q + " " + a,
                            df, n_docs)
        if terms:
            entry += f"\n  topics: {', '.join(terms)}"
    return entry, qc


def render_spans(pool, embedder, budget: int, df, n_docs, tags: bool,
                 mem_chars: int = 220, mmr: float = 0.90, k: int = 24):
    picked, pv, used, fid = [], [], [], []
    left = budget
    for i, m in enumerate(pool):
        v = _unit(m["vec"])
        if any(float(v @ p) >= mmr for p in pv):
            continue
        entry, qc = _span_entry(m, embedder, mem_chars, tags, df, n_docs)
        if len(entry) > left:
            break
        left -= len(entry)
        picked.append(entry)
        pv.append(v)
        used.append(i)
        fid.append(qc)
        if len(picked) >= k:
            break
    return "Recalled memories:\n\n" + "\n----\n".join(picked), used, fid


def clusters(pool, sim: float = 0.85) -> list[list[int]]:
    """Greedy single-pass grouping in recall order: a memory joins the
    first group whose SEED it is within `sim` of."""
    groups: list[list[int]] = []
    seeds: list[np.ndarray] = []
    for i, m in enumerate(pool):
        v = _unit(m["vec"])
        for g, s in zip(groups, seeds):
            if float(v @ s) >= sim:
                g.append(i)
                break
        else:
            groups.append([i])
            seeds.append(v)
    return groups


def render_clustered(pool, embedder, budget: int, df, n_docs,
                     mem_chars: int = 220, gist_chars: int = 90,
                     group_sim: float = 0.85, max_gists: int = 3):
    lines = ["Recalled memories (grouped: each ▸ is one topic; the first "
             "entry is its best-matching memory, 'also' lines are other "
             "memories in the same group):", ""]
    left = budget - len(lines[0])
    used = []
    for g in clusters(pool, group_sim):
        rep = pool[g[0]]
        entry, _ = _span_entry(rep, embedder, mem_chars, True, df, n_docs)
        sims = [pool[i]["sim"] for i in g]
        reasks = sum(int(pool[i].get("reask_count") or 0) for i in g)
        head = (f"▸ {len(g)} memor{'y' if len(g) == 1 else 'ies'}"
                f" · sim {max(sims):.2f}"
                + (f"–{min(sims):.2f}" if len(g) > 1 else "")
                + (f" · asked again {reasks}x in total" if reasks else ""))
        block = [head, entry]
        for i in g[1:1 + max_gists]:
            gq, _ = select_spans(pool[i]["situation"],
                                 np.asarray(pool[i]["vec"]), embedder,
                                 gist_chars)
            block.append(f"  also (sim {pool[i]['sim']:.2f}): {gq}")
        if len(g) > 1 + max_gists:
            block.append(f"  +{len(g) - 1 - max_gists} more like these")
        text = "\n".join(block)
        if len(text) + 2 > left:
            break
        left -= len(text) + 2
        lines.append(text)
        lines.append("")
        used.extend(g)
    return "\n".join(lines).rstrip(), used
