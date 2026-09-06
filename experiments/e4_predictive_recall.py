"""E4 (predictive-recall probe): does the session's momentum direction
(the ring velocity at turn t-1) predict what turn t needs?

The prefetch question -- 'can membrane act before the ask?' -- answered
with a number, before building the trajectory codec. Offline, script-first:

  Replay the audited operator turns in time order. At each turn, compute
  the momentum direction (the EMA of previous turn vectors -- the same
  shape consolidate_now nudges session:now with). Measure:

    hit@k  -- the fraction of turns where the turn's own best cell (the
              cell embedding recall ranks top-1 in the pre-turn field) lies
              within the momentum cone (cosine >= bar) of the direction.
    lift   -- the top-cell's alignment to the momentum direction vs the
              average cell's (how much the direction aims at the band).

  Three spans (EMA-1/3/5) + a random-direction control. If cone-hit beats
  random, prefetch has signal; the spans say how much memory it needs.

Run:  ~/.membrane/venv/bin/python experiments/e4_predictive_recall.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.field import ExemplarStore  # noqa: E402

AUDIT = ".membrane/audit.jsonl"
FIELD = ".membrane/field.json"
CAPTURE_PREFIX = ("tool ", "tool call", "reasoning:", "plan:", "delegated:",
                  "call ", "result ", "system-reminder")


def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--cone", type=float, default=0.55)
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

    # THE ONE-TIME MATRIX: the full field, unit-normalized, in store order.
    # A turn's pre-turn field is the fs[i]-prefix (append-only store order
    # == learn order), so top-1 = argmax over Vn[:fs[i]] @ qv -- no per-turn
    # rebuilds, just a slice-view matmul.
    V = np.array([r.vec for r in all_rows if getattr(r, "vec", None)],
                 dtype=float)
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    # store-index -> matrix-row map (rows skipped for missing vecs shift
    # indexes; build it once)
    row_of = {}
    mr = 0
    for si, r in enumerate(all_rows):
        if getattr(r, "vec", None):
            row_of[si] = mr
            mr += 1

    spans = (1, 3, 5)
    hits = {s: 0 for s in spans}
    lifts = {s: [] for s in spans}
    rand_hits = 0
    total = 0
    mom = {s: None for s in spans}
    mom_ts = {s: 0.0 for s in spans}
    rng = np.random.default_rng(7)

    for i, d in enumerate(dec):
        q = d["query"]
        fs_i = max(0, fs[i] - 1)
        if fs_i < 50:
            continue
        qv = unit(seen[q])
        sims = Vn[:fs_i] @ qv
        top_si = int(np.argmax(sims))       # store index of the top cell
        row = row_of.get(top_si)
        if row is None:
            continue
        top_vec = Vn[row]
        total += 1
        for span in spans:
            mv = mom[span]
            if mv is not None:
                align = float(mv @ top_vec)
                lifts[span].append(align)
                if align >= args.cone:
                    hits[span] += 1
                idle = max(0.0, time.time() - mom_ts[span]) / 60.0 \
                    if mom_ts[span] else 0.0
                frac = 0.95 ** (1.0 + idle)
                new = frac * mv + qv
                nn = np.linalg.norm(new)
                mom[span] = new / nn if nn > 1e-12 else new
                mom_ts[span] = time.time()
            else:
                mom[span] = qv.copy()
        rvec = unit(rng.normal(size=Vn.shape[1]))
        if float(rvec @ top_vec) >= args.cone:
            rand_hits += 1

    print(f"turns measured: {total} (cone bar {args.cone})")
    for span in spans:
        if lifts[span]:
            print(f"  momentum EMA-{span}: cone-hit "
                  f"{100 * hits[span] / total:.1f}%  "
                  f"avg align {float(np.mean(lifts[span])):.3f}")
    print(f"  random direction: cone-hit {100 * rand_hits / total:.1f}%")
    print("(cone-hit >> random => prefetch signal; EMA-1 vs EMA-5 says "
          "how much memory the predictor needs)")


if __name__ == "__main__":
    main()
