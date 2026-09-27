"""E20 -- SELECTING REAL ENTITIES, four levers against E19's weak filter.

  1. CLEAN   strip unclosed machinery tags (<system-reminder>... with no
             close tag) and BOILERPLATE: sentences that recur near-
             verbatim across many messages (found from the data, not a
             list) -- the templates E19's consistency filter could not
             tell from entities.
  2. BURST   Goh-Barabasi burstiness of a candidate's mention gaps:
             (sd - mean) / (sd + mean); -1 evenly spread, 0 random, +1
             clustered. Real entities come in bursts while being worked on.
  3. PHRASES 2-3 word content-word n-grams recurring in the head, next to
             E18's detectors (specific / known names / single concepts).
  4. SELF    leave-one-out self-retrieval on the head: does each mention
             pick its own candidate out among ALL candidates? The target
             property, scored before the test.
  +  COMBO   the mean rank over consistency, burstiness and self.

Every criterion keeps its top-K (150, 300) candidates, and every set is
compared with random same-size sets from the SAME eligible pool (>= 3
head mentions). Scored on the first 60% of the stream, measured on the
last 40% with span tracking (E18's winner): distinguishability top-1 /
top-5 and coverage.

    PYTHONPATH=. .venv/bin/python experiments/e20_entity_select.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from e18_entity_field import (_SENT, concept_vocab, detect_concept,  # noqa: E402
                              detect_known, detect_specific, embed_cached,
                              known_names, load_stream, span_of, unit)
from e19_entity_filter import alias, track  # noqa: E402
from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.slash import STOP  # noqa: E402

_UNCLOSED = re.compile(r"<(system-reminder|system-warning)>.*$", re.S | re.I)
_TOK = re.compile(r"[a-z][a-z0-9_]+")


def norm_sentence(s: str) -> str:
    return re.sub(r"\d+", "0", " ".join(s.lower().split()))[:80]


def clean_stream(stream, min_df: int):
    """Strip unclosed machinery tags, then boilerplate sentences (those
    recurring in >= min_df messages). Returns (stream, removed-examples)."""
    for m in stream:
        m["text"] = _UNCLOSED.sub("", m["text"]).strip()
        m["q"] = _UNCLOSED.sub("", m["q"]).strip()
    df = Counter()
    for m in stream:
        df.update({norm_sentence(s) for s in _SENT.split(m["text"]) if len(s) > 25})
    boiler = {s for s, c in df.items() if c >= min_df}
    for m in stream:
        keep = [s for s in _SENT.split(m["text"])
                if not (len(s) > 25 and norm_sentence(s) in boiler)]
        m["text"] = " ".join(keep).strip() or m["q"]
    ex = [s for s, _ in sorted(((s, df[s]) for s in boiler), key=lambda x: -x[1])]
    return stream, ex, len(boiler)


def phrase_vocab(stream, head: int, min_df: int = 3) -> set[str]:
    df = Counter()
    for m in stream[:head]:
        toks = _TOK.findall(m["text"].lower())
        grams = set()
        for n in (2, 3):
            for i in range(len(toks) - n + 1):
                g = toks[i:i + n]
                if all(t not in STOP and len(t) >= 3 for t in g):
                    grams.add(" ".join(g))
        df.update(grams)
    return {g for g, c in df.items() if c >= min_df}


def detect_phrases(msg, vocab: set) -> dict:
    toks = _TOK.findall(msg["text"].lower())
    out = {}
    for n in (2, 3):
        for i in range(len(toks) - n + 1):
            g = " ".join(toks[i:i + n])
            if g in vocab:
                out["phrase:" + g] = g
    # maximal phrases only: 'hard surfaced' inside 'hard surfaced
    # dimension' is the same mention, not a rival
    return {e: g for e, g in out.items()
            if not any(g != h and f" {g} " in f" {h} " for h in out.values())}


def head_stats(mentions, span_vec, split):
    """Per candidate (>= 3 head mentions): consistency, burstiness, self."""
    spans, times = defaultdict(list), defaultdict(list)
    for t in range(split):
        for e, k in mentions[t].items():
            spans[e].append(k)
            times[e].append(t)
    elig = [e for e in spans if len(set(spans[e])) >= 3]
    rng = random.Random(20)
    keys = [k for e in elig for k in spans[e]]
    bg = float(np.mean([span_vec[a] @ span_vec[b]
                        for a, b in (rng.sample(keys, 2) for _ in range(3000))]))
    cons, burst, V_of, C = {}, {}, {}, []
    for e in elig:
        V = np.array([span_vec[k] for k in dict.fromkeys(spans[e])][:40])
        V_of[e] = V
        n = len(V)
        G = V @ V.T
        cons[e] = float((G.sum() - n) / (n * (n - 1))) - bg
        gaps = np.diff(sorted(set(times[e])))
        if len(gaps) >= 2:
            mu, sd = gaps.mean(), gaps.std()
            burst[e] = float((sd - mu) / (sd + mu)) if sd + mu > 0 else 0.0
        else:
            burst[e] = 0.0
        C.append(V.sum(axis=0))
    C = np.array(C)
    Cn = unit(C)
    self_ = {}
    for i, e in enumerate(elig):
        V = V_of[e]
        hits = 0
        for v in V:
            sims = Cn @ v
            loo = unit(C[i] - v)                 # own centroid without v
            sims[i] = float(loo @ v)
            hits += int((sims > sims[i]).sum() < 5)
        self_[e] = hits / len(V)
    return elig, cons, burst, self_


def ranked(scores: dict) -> list[str]:
    return [e for e, _ in sorted(scores.items(), key=lambda x: -x[1])]


def main() -> None:
    emb = OllamaEmbedder(model="mxbai-embed-large")
    names = known_names(Path(".membrane/entities.json"))
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len,
                                               reverse=True)) + r")\b")
    results = {}
    for clean in (False, True):
        stream = load_stream(Path(".membrane/audit.jsonl"), 4000)
        split = int(0.6 * len(stream))
        if clean:
            stream, ex, nb = clean_stream(stream, min_df=max(5, len(stream) // 100))
            print(f"\nCLEAN: {nb} boilerplate sentence forms removed; most frequent:")
            for s in ex[:6]:
                print(f"   - {s}")
        concepts = concept_vocab(stream)
        phrases = phrase_vocab(stream, split)
        sets = {
            "words (E19 set)": lambda m: {**detect_specific(m),
                                          **detect_known(m, names, pat),
                                          **detect_concept(m, concepts)},
            "phrases + names": lambda m: {**detect_specific(m),
                                          **detect_known(m, names, pat),
                                          **detect_phrases(m, phrases)},
        }
        for set_name, det in sets.items():
            raw = [det(m) for m in stream]
            spans = sorted({span_of(stream[t]["text"], s)
                            for t, ents in enumerate(raw) for s in ents.values()})
            key_of = {s: i for i, s in enumerate(spans)}
            SV = embed_cached(emb, spans, "span")
            span_vec = {i: SV[i] for i in range(len(spans))}
            mentions = []
            for t, ents in enumerate(raw):
                mm = {}
                for e, s in ents.items():
                    mm.setdefault(alias(e), key_of[span_of(stream[t]["text"], s)])
                mentions.append(mm)
            elig, cons, burst, self_ = head_stats(mentions, span_vec, split)
            combo_rank = defaultdict(float)
            for sc in (cons, burst, self_):
                for r, e in enumerate(ranked(sc)):
                    combo_rank[e] += r
            orders = {"consistency": ranked(cons), "burstiness": ranked(burst),
                      "self-retrieval": ranked(self_),
                      "combined": sorted(elig, key=lambda e: combo_rank[e])}
            label = f"{'cleaned' if clean else 'raw'} stream / {set_name}"
            base = track(mentions, span_vec, split, None, True)
            print(f"\n== {label}: {base['n_states']} candidates, {len(elig)} eligible "
                  f"(>= 3 head mentions); no filter: top-1 {base['top1']:.1%} "
                  f"top-5 {base['top5']:.1%}")
            print(f"   {'keep top-K by':<16} {'K':>4} {'coverage':>9} {'top-1':>7} "
                  f"{'top-5':>7} | {'random top-1':>12} {'gain':>6}")
            res = {"no_filter": base, "eligible": len(elig)}
            for K in (150, 300):
                if K > len(elig):
                    continue
                ctl = [track(mentions, span_vec, split,
                             set(random.Random(s).sample(elig, K)), True) for s in range(5)]
                c1 = float(np.mean([c["top1"] for c in ctl]))
                c5 = float(np.mean([c["top5"] for c in ctl]))
                cc = float(np.mean([c["coverage"] for c in ctl]))
                print(f"   {'random (control)':<16} {K:>4} {cc:>8.0%} {c1:>7.1%} "
                      f"{c5:>7.1%} |")
                for crit, order in orders.items():
                    got = track(mentions, span_vec, split, set(order[:K]), True)
                    print(f"   {crit:<16} {K:>4} {got['coverage']:>8.0%} "
                          f"{got['top1']:>7.1%} {got['top5']:>7.1%} | {c1:>12.1%} "
                          f"{got['top1'] - c1:>+6.1%}")
                    res[f"{crit}@{K}"] = {**got, "control_top1": c1}
            print("   combined top 15:", ", ".join(e[2:] for e in orders["combined"][:15]))
            print("   combined bottom 8:", ", ".join(e[2:] for e in orders["combined"][-8:]))
            results[label] = res
    Path(".membrane/e20b-results.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
