"""E16 -- THE FIDELITY SEARCH: can we edit a lossy text so the embedder
reconstructs the field vector it came from -- and does the gain carry
to a model the search never saw?

Setup (reuses E15's corpus + cached paraphrases): for each target
memory, the TARGET vector is the field's vector of the original text;
the START text is a paraphrase of it (a lossy rendering). The search
(membrane/fidelity.py) edits the paraphrase under the SEARCH embedder
(mxbai) toward the target. The honest score is the HELD-OUT embedder
(qwen3-embedding): cosine to its own vector of the original, and the
original's retrieval rank in its own corpus index. A gain mxbai sees and
qwen does not is an adversarial artifact, not a better rendering.

Region words come from the target's stored neighbours (the target and
its near-dups excluded -- no copying the answer); this is the RENDERING
setting, where the memory's vector is known.

    PYTHONPATH=. .venv/bin/python experiments/e16_fidelity.py [--n 60] [--ablate]

--ablate re-runs the search with each operator alone (which operators
does the embedder actually read?). Caches live under .membrane/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np

from membrane.embed import OllamaEmbedder
from membrane.fidelity import OPS, fit, region_words, _unit
from membrane.slash import SlashVocab
from membrane.token_timeline import tokenize

CACHE = Path(".membrane")


def corpus_matrix(emb, texts: list[str]) -> np.ndarray:
    """The E15 cache (same key), one matrix per embedder."""
    h = hashlib.sha256(("\x00".join(texts) + emb.model).encode()).hexdigest()[:12]
    path = CACHE / f"e15-corpus-{h}.npy"
    if path.exists():
        return np.load(path)
    out = []
    for lo in range(0, len(texts), 32):
        out.extend(emb.embed(texts[lo:lo + 32]))
        if lo % 320 == 0:
            print(f"  [corpus {emb.model}] {lo}/{len(texts)}", file=sys.stderr)
    M = _unit(out)
    np.save(path, M)
    return M


def clean_prompt(s: str) -> bool:           # E15's target filter
    low = s.lower()
    return (40 <= len(s) <= 300 and not low.startswith("tool ")
            and "<system-reminder>" not in low and "```" not in s
            and len(s.split()) >= 6)


def strip_preamble(out: str) -> str:        # E15's paraphrase cleanup
    first, _, rest = out.partition("\n")
    if rest.strip() and first.lower().startswith(("here is", "here's")):
        return rest.strip().strip('"')
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default=".membrane/field.json.migrated")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=15)
    ap.add_argument("--search-model", default="mxbai-embed-large")
    ap.add_argument("--judge-model", default="qwen3-embedding:4b")
    ap.add_argument("--para-cache", default=".membrane/e15-para-llama3.1_8b.json")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--ablate", action="store_true")
    ap.add_argument("--show", type=int, default=6)
    args = ap.parse_args()

    rows = json.loads(Path(args.field).read_text())
    corpus = list(dict.fromkeys(r["situation"] for r in rows
                                if (r.get("situation") or "").strip()))
    rng = random.Random(args.seed)
    pool = [i for i, s in enumerate(corpus) if clean_prompt(s)]
    paras_all = json.loads(Path(args.para_cache).read_text())
    # E15's sample order, restricted to targets it already paraphrased
    targets = [i for i in rng.sample(pool, len(pool))
               if corpus[i] in paras_all][:args.n]
    paras = [strip_preamble(paras_all[corpus[i]]) for i in targets]

    se = OllamaEmbedder(model=args.search_model)
    je = OllamaEmbedder(model=args.judge_model)
    MS = corpus_matrix(se, corpus)
    MJ = corpus_matrix(je, corpus)
    vocab = SlashVocab(se, corpus)
    print(f"corpus {len(corpus)}, targets {len(targets)}, "
          f"vocab {len(vocab.words)}", file=sys.stderr)

    def context(ti: int, para: str):
        """Region words (the target's stored neighbours) + confusers
        (where the paraphrase lands instead)."""
        v = MS[ti]
        sims = MS @ v
        nb = [j for j in np.argsort(-sims)[1:40]
              if j != ti and sims[j] < 0.97][:10]
        have = set(tokenize(para))
        region = region_words([corpus[j] for j in nb], exclude=have)
        pv = _unit(se.embed([para])[0])
        wrong = [j for j in np.argsort(-(MS @ pv))[:10]
                 if j != ti and sims[j] < 0.80][:5]
        conf = region_words([corpus[j] for j in wrong],
                            exclude=have | set(region), n=8)
        return region, conf

    ctxs = [context(ti, p) for ti, p in zip(targets, paras)]

    def judge(texts: list[str]):
        """Held-out: cos to the judge's vector of the original + the
        original's rank in the judge's own index."""
        Q = _unit(je.embed(texts))
        S = Q @ MJ.T
        idx = np.arange(len(texts)), np.array(targets)
        return S[idx], (S > S[idx][:, None]).sum(axis=1)

    def search_side(texts: list[str]):
        Q = _unit(se.embed(texts))
        S = Q @ MS.T
        idx = np.arange(len(texts)), np.array(targets)
        return S[idx], (S > S[idx][:, None]).sum(axis=1)

    base_s, base_sr = search_side(paras)
    base_j, base_jr = judge(paras)
    print(f"\n{'run':<10} {'S-cos':>13} {'S-top1':>13} | "
          f"{'J-cos':>13} {'J-top1':>13} {'J+':>3} {'J-':>3} {'edits':>5}")
    print(f"{'para':<10} {base_s.mean():>13.3f} {(base_sr == 0).mean():>13.1%} | "
          f"{base_j.mean():>13.3f} {(base_jr == 0).mean():>13.1%}")

    runs = [("all", OPS)]
    if args.ablate:
        runs += [(op, (op,)) for op in OPS]
    kept = {}
    for name, ops in runs:
        fitted, used = [], Counter()
        for n, (ti, p, (reg, conf)) in enumerate(zip(targets, paras, ctxs)):
            t, trace = fit(p, MS[ti], se, vocab, reg, conf, ops=ops,
                           rounds=args.rounds)
            fitted.append(t)
            used.update(s["op"].split(" ", 1)[0] for s in trace[1:])
            if n % 20 == 0:
                print(f"  [{name}] {n}/{len(targets)}", file=sys.stderr)
        s_cos, s_r = search_side(fitted)
        j_cos, j_r = judge(fitted)
        kept[name] = (fitted, j_cos, j_r)
        print(f"{name:<10} "
              f"{s_cos.mean():>6.3f} ({s_cos.mean() - base_s.mean():+.3f}) "
              f"{(s_r == 0).mean():>6.1%} ({(s_r == 0).mean() - (base_sr == 0).mean():+.0%}) | "
              f"{j_cos.mean():>6.3f} ({j_cos.mean() - base_j.mean():+.3f}) "
              f"{(j_r == 0).mean():>6.1%} ({(j_r == 0).mean() - (base_jr == 0).mean():+.0%}) "
              f"{((base_jr > 0) & (j_r == 0)).sum():>3} "
              f"{((base_jr == 0) & (j_r > 0)).sum():>3} "
              f"{sum(used.values()):>5}")
        if name == "all":
            print(f"           accepted ops: {dict(used.most_common())}")

    fitted, j_cos, j_r = kept["all"]
    gain = j_cos - base_j
    print("\nlargest held-out gains / losses (run 'all'):")
    order = list(np.argsort(-gain)[:args.show // 2]) + \
        list(np.argsort(gain)[:args.show - args.show // 2])
    for i in order:
        print(f"  J {base_j[i]:.3f} -> {j_cos[i]:.3f} (rank {base_jr[i]} -> {j_r[i]})\n"
              f"    orig:  {corpus[targets[i]][:110]!r}\n"
              f"    para:  {paras[i][:110]!r}\n"
              f"    fit:   {fitted[i][:150]!r}")


if __name__ == "__main__":
    main()
