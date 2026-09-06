"""E3 (reason-graph probe): can multi-hop walks over the ENTITY GRAPH answer
turns that 1-hop embedding recall puts in the uncertain band?

The B2 question -- 'could graph reasoning pick up part of the 96.3% model
band?' -- answered with a number. For every audited operator turn whose
direct recall sits in the uncertain band (0.50-0.90, the E2 finding):

  1. SEED: the turn's harvested entities (query_entities) present in the
     entity index.
  2. WALK: 2-hop personalized traversal over the CO-OCCURRENCE graph
     (entity keys sharing cells; edges = shared-cell counts, the only
     honest edge -- cooccurrence_neighbors).
  3. GATHER: the walk's cells (deduped) -- candidate 'reasoned' evidence
     the embedding alone did not rank top-k.
  4. GRADE: does the gathered evidence include a cell whose ACTION is
     near the model's actual answer (token overlap >= 0.7 OR action-
     cosine >= 0.85 -- the E1/E2 bar)? Count REASON-ANSWERED turns:
     uncertain-band turns where the graph surfaces the answer.

Also reports the graph's shape (entities, edges, orphan rate) -- an
instrument for the second-layer question (trajectories over time: the
walk needs edges worth walking; the shape read says whether they exist).

Run:  ~/.membrane/venv/bin/python experiments/e3_reason_graph.py [--limit N]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.entities import (entity_index, iter_entities,  # noqa: E402
                               cooccurrence_neighbors, query_entities)
from membrane.field import ExemplarStore  # noqa: E402

AUDIT = ".membrane/audit.jsonl"
FIELD = ".membrane/field.json"

CAPTURE_PREFIX = ("tool ", "tool call", "reasoning:", "plan:", "delegated:",
                  "call ", "result ", "system-reminder")


def jaccard(a: str, b: str) -> float:
    ta = {w.lower() for w in (a or "").split()}
    tb = {w.lower() for w in (b or "").split()}
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) + 1e-12)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sim-act", type=float, default=0.90)
    ap.add_argument("--sim-null", type=float, default=0.50)
    ap.add_argument("--batch", type=int, default=128)
    args = ap.parse_args()

    recs = [json.loads(ln) for ln in open(AUDIT)]
    dec = [d for d in recs
           if d.get("event") == "decision" and d.get("query")
           and d.get("response")
           and not str(d.get("query", "")).lstrip().startswith(
               ("membrane:", "model:"))
           and "compact" not in str(d.get("verdict", ""))]
    if args.limit:
        dec = dec[:args.limit]

    store = ExemplarStore.load(FIELD)
    all_rows = store.exemplars
    fs = [int(d.get("field_size") or 0) for d in dec]

    inv = entity_index(all_rows)
    n_ents = len(inv)
    edges = sum(len(cooccurrence_neighbors(all_rows, k, inv=inv,
                                           min_links=1))
                for k in list(inv)[:2000])
    orphans = sum(1 for k in inv
                  if not cooccurrence_neighbors(all_rows, k, inv=inv,
                                                min_links=1))
    print(f"entity graph: {n_ents} entities, "
          f"{edges / max(2000, 1):.1f} edges/entity (sample), "
          f"{100 * orphans / max(n_ents, 1):.0f}% orphans (no edges)")

    emb = OllamaEmbedder()
    seen: dict[str, np.ndarray] = {}

    def em(texts: list[str]) -> None:
        todo = sorted({t for t in texts if t not in seen})
        for k in range(0, len(todo), args.batch):
            chunk = todo[k:k + args.batch]
            try:
                for t, v in zip(chunk, emb.embed(chunk)):
                    seen[t] = np.asarray(v, dtype=float)
            except Exception:
                for t in chunk:
                    seen.setdefault(t, np.zeros(1024))

    em([d["query"] for d in dec])
    em([r.action for r in all_rows if r.action])
    em([r.situation for r in all_rows if r.situation])

    stats: collections.Counter = collections.Counter()
    wins: list = []
    for i, d in enumerate(dec):
        q = d["query"]
        pool = all_rows[:max(0, fs[i] - 1)]
        pool = [r for r in pool
                if not r.ephemeral and r.safe and r.action
                and not r.situation.startswith(CAPTURE_PREFIX)
                and r.situation != q]
        if not pool:
            continue
        V = np.array([r.vec for r in pool], dtype=float)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        qv = unit(seen[q])
        sims = Vn @ qv
        s = float(np.max(sims))
        if not (args.sim_null <= s < args.sim_act):
            continue                    # only the uncertain band
        stats["band"] += 1
        resp = d["response"].strip()

        # ---- the 2-hop reason walk ----
        # the walk uses a PREFIX-SCOPED index: entity_index(pool) recomputed
        # per fs would be O(n^2); instead filter the GLOBAL inv's neighbor
        # cell ids by the fs prefix (the walk's hop-2 gather below does the
        # same), and guard gather indexes against the pool length (a prefix
        # pool is shorter than the final store -- inv indexes the store)
        seeds = [k for k in query_entities(q) if k in inv and
                 any(ix < fs[i] for ix in inv[k])]
        gathered: set[int] = set()
        for k in seeds[:4]:             # bounded: the walk is cheap
            for ix in inv[k]:
                if ix < fs[i]:
                    gathered.add(ix)
            for nk, _c in cooccurrence_neighbors(all_rows, k, inv=inv,
                                                 min_links=2):
                for ix in inv.get(nk) or []:
                    if ix < fs[i]:
                        gathered.add(ix)
        # grade: any gathered cell answers what the model answered
        hit = False
        for ix in gathered:
            a = pool[ix].action if ix < len(pool) else None
            if not a:
                continue
            if jaccard(a, resp) >= 0.7:
                hit = True
                break
            av, rv = seen.get(a), seen.get(resp)
            if av is not None and rv is not None \
                    and float(unit(av) @ unit(rv)) >= 0.85:
                hit = True
                break
        if hit:
            stats["reason-answered"] += 1
            if len(wins) < 10:
                wins.append((q, resp))

    band = max(stats["band"], 1)
    print(f"uncertain-band turns: {stats['band']}")
    print(f"reason-answered by 2-hop walk: {stats['reason-answered']} "
          f"({100 * stats['reason-answered'] / band:.1f}% of the band)")
    print(f"(the walk gathers cells through SHARED-TAG edges -- evidence "
          f"the embedding ranks below the band, reachable by relation)")
    if wins:
        print("\nsample wins:")
        for q, resp in wins[:5]:
            print(f"  Q: {q[:70]!r} -> model said: {resp[:60]!r}")


if __name__ == "__main__":
    main()
