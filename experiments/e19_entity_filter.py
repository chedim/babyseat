"""E19 -- WHICH CANDIDATES ARE REAL ENTITIES? The mention-consistency
filter + alias merging, measured on distinguishability (E18's decisive
number) with span tracking (E18's winning method).

  consistency  a candidate is an entity only if its mention sentences
               agree with each other MORE than random sentences of this
               stream do: mean pairwise cos of its spans minus the
               background mean (random span pairs). Scored on the first
               60% of the stream only -- the tail is never peeked at.
  aliases      rule-based merge: file paths -> basename, case, -/_ ->
               space, a trailing plural 's'.
  control      filtering shrinks the candidate set, and picking the right
               entity among fewer is easier by itself -- so every filtered
               set is compared with RANDOM subsets of the same size drawn
               from the same eligible pool. Only the gain over that counts.

Distinguishability: each tail mention of an already-tracked entity --
does its sentence rank that entity first (top-1) / in the top 5 among
ALL tracked states? Coverage: the share of tail messages that still
carry at least one entity.

    PYTHONPATH=. .venv/bin/python experiments/e19_entity_filter.py

Reuses E18's caches (stream, message + span embeddings).
"""

from __future__ import annotations

import random
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from e18_entity_field import (concept_vocab, detect_concept,  # noqa: E402
                              detect_known, detect_specific, embed_cached,
                              known_names, load_stream, span_of, unit)
from membrane.embed import OllamaEmbedder  # noqa: E402


def alias(eid: str) -> str:
    kind, _, name = eid.partition(":")
    if kind == "files":
        name = name.rsplit("/", 1)[-1]
    name = re.sub(r"[-_\s]+", " ", name.lower()).strip()
    if len(name) > 4 and name.endswith("s") and not name.endswith("ss"):
        name = name[:-1]
    # one namespace: 'files:opencode.py', 'known:opencode.py' and
    # 'symbols:opencode.py' name the same thing
    return "e:" + name


def track(mentions, span_vec, split, keep: set | None,
          exclude_comentions: bool = False):
    """Span tracking over the stream; returns (ranks, covered_tail, n_tail,
    n_states). `keep` restricts which entity ids exist at all."""
    S: dict[str, np.ndarray] = {}
    ranks, covered, n_tail = [], 0, 0
    for t, ents in enumerate(mentions):
        ents = {e: k for e, k in ents.items() if keep is None or e in keep}
        if t >= split:
            n_tail += 1
            covered += bool(ents)
            known = [e for e in ents if e in S]
            if known and len(S) > 20:
                ids = list(S)
                M = np.array([S[e] for e in ids])
                pos = {e: i for i, e in enumerate(ids)}
                others = [pos[o] for o in known] if exclude_comentions else []
                for e in known:
                    sims = M @ span_vec[ents[e]]
                    if others:        # co-mentioned entities share the
                        sims = sims.copy()          # sentence: not rivals
                        sims[[i for i in others if i != pos[e]]] = -np.inf
                    ranks.append(int((sims > sims[pos[e]]).sum()))
        for e, k in ents.items():
            v = span_vec[k]
            S[e] = v.copy() if e not in S else unit(0.5 * S[e] + 0.5 * v)
    r = np.array(ranks)
    return {"top1": float((r == 0).mean()) if len(r) else float("nan"),
            "top5": float((r < 5).mean()) if len(r) else float("nan"),
            "n_mentions": len(r), "coverage": covered / max(n_tail, 1),
            "n_states": len(S)}


def consistency(mentions, span_vec, split, rng):
    """Per entity: mean pairwise cos of its HEAD mention spans, minus the
    background (random head span pairs). Entities with < 2 distinct head
    spans get no score (ineligible)."""
    spans = defaultdict(list)
    for t in range(split):
        for e, k in mentions[t].items():
            spans[e].append(k)
    all_keys = [k for ks in spans.values() for k in ks]
    bg = np.mean([float(span_vec[a] @ span_vec[b]) for a, b in
                  (rng.sample(all_keys, 2) for _ in range(3000))])
    score = {}
    for e, ks in spans.items():
        ks = list(dict.fromkeys(ks))
        if len(ks) < 2:
            continue
        V = np.array([span_vec[k] for k in ks[:40]])
        G = V @ V.T
        n = len(V)
        score[e] = float((G.sum() - n) / (n * (n - 1))) - bg
    return score, float(bg)


def main() -> None:
    emb = OllamaEmbedder(model="mxbai-embed-large")
    stream = load_stream(Path(".membrane/audit.jsonl"), 4000)
    split = int(0.6 * len(stream))
    names = known_names(Path(".membrane/entities.json"))
    pat = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len,
                                               reverse=True)) + r")\b")
    vocab = concept_vocab(stream)
    raw = [{**detect_specific(m), **detect_known(m, names, pat),
            **detect_concept(m, vocab)} for m in stream]
    keys, key_of = [], {}
    for t, m in enumerate(stream):
        for e, surf in raw[t].items():
            sp = span_of(m["text"], surf)
            if sp not in key_of:
                key_of[sp] = len(keys)
                keys.append(sp)
    SV = embed_cached(emb, keys, "span")          # E18's cache: no new embeds
    span_vec = {i: SV[i] for i in range(len(keys))}

    def mention_map(merge: bool):
        out = []
        for t, ents in enumerate(raw):
            m = {}
            for e, s in ents.items():
                k = key_of[span_of(stream[t]["text"], s)]
                m.setdefault(alias(e) if merge else e, k)
            out.append(m)
        return out

    rng = random.Random(19)
    print(f"stream {len(stream)} messages, head {split}, tail {len(stream) - split}")
    rows = []
    for merge in (False, True):
        mentions = mention_map(merge)
        score, bg = consistency(mentions, span_vec, split, rng)
        eligible = sorted(score)
        base = track(mentions, span_vec, split, None)
        tag = "aliases merged" if merge else "raw ids"
        print(f"\n== {tag}: {base['n_states']} candidate entities; background "
              f"span-pair cos {bg:.3f}; {len(eligible)} with >= 2 head mentions")
        print(f"   {'filter':<22} {'entities':>8} {'coverage':>9} {'top-1':>7} "
              f"{'top-5':>7} | {'random same-size top-1':>22} {'gain':>6}")
        print(f"   {'none (all candidates)':<22} {base['n_states']:>8} "
              f"{base['coverage']:>8.0%} {base['top1']:>7.1%} {base['top5']:>7.1%} |")
        rows.append({"merge": merge, "filter": None, **base})
        for delta in (0.0, 0.03, 0.06, 0.10, 0.15):
            keep = {e for e in eligible if score[e] >= delta}
            if len(keep) < 25:
                continue
            got = track(mentions, span_vec, split, keep)
            ctl = [track(mentions, span_vec, split,
                         set(random.Random(s).sample(eligible, len(keep))))
                   for s in range(3)]
            c1 = float(np.nanmean([c["top1"] for c in ctl]))
            print(f"   {'consistency >= +' + format(delta, '.2f'):<22} {len(keep):>8} "
                  f"{got['coverage']:>8.0%} {got['top1']:>7.1%} {got['top5']:>7.1%} | "
                  f"{c1:>22.1%} {got['top1'] - c1:>+6.1%}")
            rows.append({"merge": merge, "filter": delta, "control_top1": c1, **got})
        # which candidates the filter rejects / keeps, for the eye
        if merge:
            srt = sorted(score.items(), key=lambda x: x[1])
            print("   most INCONSISTENT (rejected first):",
                  ", ".join(f"{e[2:]} ({s:+.2f})" for e, s in srt[:12]))
            print("   most CONSISTENT:",
                  ", ".join(f"{e[2:]} ({s:+.2f})" for e, s in srt[-12:]))
    import json
    Path(".membrane/e19-results.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
