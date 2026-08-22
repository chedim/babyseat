"""Rank-growth experiment (#2): does a single-domain field SATURATE into a low-rank
manifold as it grows, or is mxbai inherently high-rank?

The real-embedding test (#1) found a 15-snippet field high-rank (rank_ratio 0.69)
-> novelty OFF. The open question: was that because the field was too SMALL (each
snippet a distinct concept, already spanning the domain's directions), such that
a LARGER field of the same domain would SATURATE (new snippets are variations of
covered concepts -> effective_rank stops growing while N grows -> rank_ratio
drops below 0.5 -> novelty earns a general role)? Or is mxbai inherently high-rank
(rank grows ~N, novelty stays a narrow specialist forever)?

We extract a pool of real MongoDB-Python call lines from rosetta-check's corpus,
grow the field over increasing N (seeded subsets), and watch effective_rank and
rank_ratio. If rank_ratio drops below 0.5 as N grows, the domain saturates and
novelty is valid on a saturated field (the too-small field was the problem). If
rank_ratio stays high, mxbai is inherently high-rank and novelty stays a
narrow-domain specialist.

Run:  PYTHONPATH=. python experiments/rank_growth.py   (needs local Ollama + mxbai-embed-large)
"""

from __future__ import annotations

import re
import numpy as np

from membrane.embed import OllamaEmbedder
from membrane.field import Exemplar, ExemplarStore
from membrane.variety import effective_rank

CORPUS = "/Users/dmitrii.chechetkin/projects/rosetta-check/src/test/resources/cortex-corpus/python"
CALL_RE = re.compile(r"\w+\.\w+\(")          # a method call: foo.bar(


def extract_pool() -> list[str]:
    """Real MongoDB-Python call lines from the corpus, deduped."""
    import os
    lines = []
    for fn in sorted(os.listdir(CORPUS)):
        if not fn.endswith(".py"):
            continue
        with open(os.path.join(CORPUS, fn), encoding="utf-8") as f:
            for ln in f.read().splitlines():
                s = ln.strip()
                if len(s) < 12 or len(s) > 200:
                    continue
                if not CALL_RE.search(s):
                    continue
                if s.startswith("#") or s.startswith("import"):
                    continue
                lines.append(s)
    # dedup, keep order
    seen = set(); out = []
    for s in lines:
        if s in seen: continue
        seen.add(s); out.append(s)
    return out


def sparkline(xs):
    bars = " ▁▂▃▄▅▆▇█"
    if not xs: return ""
    lo, hi = min(xs), max(xs)
    span = hi - lo
    return "".join(bars[int((x - lo) / span * 7)] if span > 0 else "▄" for x in xs)


def main() -> None:
    pool = extract_pool()
    print(f"extracted {len(pool)} real MongoDB-Python call lines from the corpus\n")
    if len(pool) < 16:
        print("pool too small for a curve; aborting."); return

    emb = OllamaEmbedder()
    print(f"embedding the pool via {emb.model} ...")
    vecs = emb.embed(pool)
    print(f"embedding dim = {vecs.shape[1]}\n")

    rng = np.random.default_rng(3)
    idx = np.arange(len(pool))
    rng.shuffle(idx)

    sizes = sorted({min(n, len(pool)) for n in [8, 16, 32, 64, 128, len(pool)]})
    rows = []
    print(f"{'N':>5} {'eff_rank':>9} {'rank_ratio':>11} {'novelty':>10}")
    print("-" * 42)
    for n in sizes:
        sub = vecs[idx[:n]]
        store = ExemplarStore()
        for v in sub:
            store.append(Exemplar("mongo-python", "actA", "ok", v.tolist(), safe=True))
        r = effective_rank(store)
        ratio = r / max(n, 1)
        status = "ON" if ratio < 0.5 and n >= 3 else "OFF"
        rows.append((n, r, ratio, status))
        print(f"{n:>5} {r:>9.2f} {ratio:>11.3f} {status:>10}")

    print()
    ratioline = [r[2] for r in rows]
    rankline = [r[1] for r in rows]
    print(f"rank_ratio vs N:  {sparkline(ratioline)}   (down -> domain saturates)")
    print(f"eff_rank   vs N:  {sparkline(rankline)}   (flat -> rank stops growing)\n")

    # verdict
    last = rows[-1]; first = rows[0]
    if last[2] < 0.5:
        print(f"VERDICT: at N={last[0]} rank_ratio={last[2]:.2f} < 0.5 -> the domain SATURATES.")
        print("  A large single-domain field IS low-rank; novelty earns a general role on a")
        print("  saturated field. The too-small field was the problem, not the metric.\n")
        print("  -> NEXT: re-run the novelty filter/knee on this large field's workload and")
        print("     confirm a real valley emerges where the 15-snippet blob had none.")
    elif last[2] > first[2]:
        print(f"VERDICT: rank_ratio rose/stayed high ({first[2]:.2f} -> {last[2]:.2f}) -> mxbai is")
        print("  INHERENTLY high-rank for this domain; effective_rank grows with N. Novelty stays")
        print("  a narrow-domain specialist; cosine-primary is the right general gate.\n")
        print("  -> the null-space metric has no general role; retire it to narrow trained fields.")
    else:
        print(f"VERDICT (growth axis): rank_ratio roughly flat ({first[2]:.2f} -> {last[2]:.2f}, never < 0.5)")
        print("  -> a WHOLE-DOMAIN field stays high-rank up to the N the corpus supports; novelty")
        print("     does NOT earn a role on a whole-domain field. (Corpus limits N to "
              f"{len(pool)}; saturation at N>>{len(pool)} untested.)\n")

    # ---- narrowing axis: split the pool by operation -> narrow sub-fields ----
    # rosetta-check's operator worked on a NARROW field (one receiver's events), not a
    # whole domain. If narrow sub-fields go low-rank, novelty is a narrow-field
    # specialist -- valid on one operation, off on the whole domain.
    print("== narrowing axis: rank_ratio per single-operation sub-field ==")
    def op_of(s):
        t = s.lower()
        for key in ("aggregate", "find", "insert", "update", "delete", "replace",
                    "watch", "change_stream", "transaction", "index", "vector_search",
                    "decimal128"):
            if key in t:
                return key
        return "other"

    groups: dict[str, list[int]] = {}
    for i, s in enumerate(pool):
        groups.setdefault(op_of(s), []).append(i)
    print(f"{'operation':>12} {'N':>4} {'eff_rank':>9} {'rank_ratio':>11} {'novelty':>8}")
    print("-" * 50)
    any_narrow = False
    for op in sorted(groups, key=lambda o: -len(groups[o])):
        gi = groups[op]
        if len(gi) < 3:
            continue
        sub = vecs[gi]
        store = ExemplarStore()
        for v in sub:
            store.append(Exemplar("mongo-python", "actA", "ok", v.tolist(), safe=True))
        r = effective_rank(store); ratio = r / max(len(gi), 1)
        status = "ON" if ratio < 0.5 else "OFF"
        if ratio < 0.5: any_narrow = True
        print(f"{op:>12} {len(gi):>4} {r:>9.2f} {ratio:>11.3f} {status:>8}")
    print()
    if any_narrow:
        print("  -> narrow single-operation sub-fields ARE low-rank (novelty ON). Combined with")
        print("     the growth axis (whole-domain stays high-rank), the answer is: novelty is a")
        print("     NARROW-FIELD specialist -- valid on one operation/receiver, off on the whole")
        print("     domain. This matches rosetta-check's tiering exactly (operator on a narrow")
        print("     trained field, cosine default). mxbai is high-rank per domain, low-rank per")
        print("     operation. The gate's conditional (rank_ratio<0.5) already encodes this.")
    else:
        print("  -> even narrow sub-fields stay high-rank here; mxbai is high-rank down to the")
        print("     operation level. Novelty has no general role; cosine-primary stands alone.")


if __name__ == "__main__":
    main()