"""E21 -- IS AN ENTITY WHAT HAS MASS? A candidate's updates should agree
with where it already is: the state (running mean of its mention
sentences -- each update moves it by (mention - state) / (n+1), so
accumulated evidence IS inertia) predicts its NEXT mention.

  alignment  mean over head mentions of cos(new mention, current state)
             minus the background (random mention pairs): does the state
             predict its next update?
  mass       the accumulated AGREEING evidence: sum of positive alignments

Same frame as E20 (the cleaned stream; words and phrases candidate sets;
top-K per criterion vs random same-size sets from the same eligible pool;
tail distinguishability with co-mentions excluded) -- consistency and
E20's combo are re-run beside it for a like-for-like comparison.

    PYTHONPATH=. .venv/bin/python experiments/e21_entity_mass.py
"""

from __future__ import annotations

import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from e18_entity_field import (concept_vocab, detect_concept, detect_known,  # noqa: E402
                              detect_specific, embed_cached, known_names,
                              load_stream, span_of, unit)
from e19_entity_filter import alias, track  # noqa: E402
from e20_entity_select import (clean_stream, detect_phrases, head_stats,  # noqa: E402
                               phrase_vocab, ranked)
from membrane.embed import OllamaEmbedder  # noqa: E402


def mass_stats(mentions, span_vec, split, elig):
    """Online, head only: alignment (mean) and mass (sum of positives)."""
    seq = defaultdict(list)
    for t in range(split):
        for e, k in mentions[t].items():
            seq[e].append(k)
    rng = random.Random(21)
    keys = [k for e in elig for k in seq[e]]
    bg = float(np.mean([span_vec[a] @ span_vec[b]
                        for a, b in (rng.sample(keys, 2) for _ in range(3000))]))
    align, mass, net, accr = {}, {}, {}, {}
    for e in elig:
        ks = list(dict.fromkeys(seq[e]))
        s, n, al = span_vec[ks[0]].copy(), 1, []
        for k in ks[1:]:
            v = span_vec[k]
            al.append(float(unit(s) @ v) - bg)
            s = s + (v - s) / (n + 1)          # the inertial update
            n += 1
        align[e] = float(np.mean(al)) if al else 0.0
        mass[e] = float(sum(a for a in al if a > 0))   # biased: noise's
        # positive halves accumulate with mention count
        net[e] = float(sum(al))                          # noise cancels
        accr[e] = float(sum(1 for a in al if a >= ACCRETE))  # clear agreement
    return align, mass, net, accr, bg


ACCRETE = 0.15     # an update 'sticks' when it agrees this clearly


def main() -> None:
    emb = OllamaEmbedder(model="mxbai-embed-large")
    names = known_names(Path(".membrane/entities.json"))
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len,
                                               reverse=True)) + r")\b")
    stream = load_stream(Path(".membrane/audit.jsonl"), 4000)
    split = int(0.6 * len(stream))
    stream, _, _ = clean_stream(stream, min_df=max(5, len(stream) // 100))
    concepts = concept_vocab(stream)
    phrases = phrase_vocab(stream, split)
    sets = {
        "words": lambda m: {**detect_specific(m), **detect_known(m, names, pat),
                            **detect_concept(m, concepts)},
        "phrases + names": lambda m: {**detect_specific(m), **detect_known(m, names, pat),
                                      **detect_phrases(m, phrases)},
    }
    for set_name, det in sets.items():
        raw = [det(m) for m in stream]
        spans = sorted({span_of(stream[t]["text"], s)
                        for t, ents in enumerate(raw) for s in ents.values()})
        key_of = {s: i for i, s in enumerate(spans)}
        SV = embed_cached(emb, spans, "span")
        span_vec = {i: SV[i] for i in range(len(spans))}
        mentions = [{} for _ in raw]
        for t, ents in enumerate(raw):
            for e, s in ents.items():
                mentions[t].setdefault(alias(e), key_of[span_of(stream[t]["text"], s)])
        elig, cons, burst, self_ = head_stats(mentions, span_vec, split)
        align, mass, net, accr, bg = mass_stats(mentions, span_vec, split, elig)
        combo = defaultdict(float)
        for sc in (cons, burst, self_):
            for r, e in enumerate(ranked(sc)):
                combo[e] += r
        orders = {"consistency": ranked(cons),
                  "E20 combined": sorted(elig, key=lambda e: combo[e]),
                  "alignment": ranked(align), "mass (biased)": ranked(mass),
                  "net mass": ranked(net), "accreted mass": ranked(accr)}
        print(f"\n== cleaned stream / {set_name}: {len(elig)} eligible; "
              f"background mention-pair cos {bg:.3f}")
        print(f"   {'keep top-K by':<14} {'K':>4} {'coverage':>9} {'top-1':>7} "
              f"{'top-5':>7} | {'random top-1':>12} {'gain':>6}")
        for K in (100, 150, 300):
            if K > len(elig):
                continue
            ctl = [track(mentions, span_vec, split,
                         set(random.Random(s).sample(elig, K)), True) for s in range(5)]
            c1 = float(np.mean([c["top1"] for c in ctl]))
            print(f"   {'random':<14} {K:>4} "
                  f"{np.mean([c['coverage'] for c in ctl]):>8.0%} {c1:>7.1%} "
                  f"{np.mean([c['top5'] for c in ctl]):>7.1%} |")
            for crit, order in orders.items():
                g = track(mentions, span_vec, split, set(order[:K]), True)
                print(f"   {crit:<14} {K:>4} {g['coverage']:>8.0%} {g['top1']:>7.1%} "
                      f"{g['top5']:>7.1%} | {c1:>12.1%} {g['top1'] - c1:>+6.1%}")
        vals = {"alignment": align, "net mass": net, "accreted mass": accr}
        for crit, sc in vals.items():
            o = orders[crit]
            print(f"   {crit} top 20: " + ", ".join(
                f"{e[2:]} ({sc[e]:.2f})" for e in o[:20]))
            print(f"   {crit} bottom 8: " + ", ".join(e[2:] for e in o[-8:]))


if __name__ == "__main__":
    main()
