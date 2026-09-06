"""E5 (prefetch latency probe): what does the momentum-cone prefetch lane
actually buy, in wall-clock?

E4 proved the signal (97.3% cone-hit). This probe measures the two cost
sides of acting on it, against the live field:

  COLD (today's path):  embed the query (37ms) -> top-1 over the field
  WARM (prefetch lane): pre-embed + pre-rank the cone's cells during idle;
                        at turn time only re-rank the cone's shortlist

Measured on the live field + ollama embedder, one pass, no simulation of
traffic -- the numbers ARE the argument (or the disproof).

Run:  ~/.membrane/venv/bin/python experiments/e5_prefetch_latency.py
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.embed import OllamaEmbedder  # noqa: E402
from membrane.field import ExemplarStore  # noqa: E402

FIELD = ".membrane/field.json"

PROBES = [
    "why does the gate use consensus instead of a single vote",
    "how does the ephemeral tier promote candidates to canon",
    "what changed in the peers substrate today",
    "explain the null-recall lane and when it fires",
    "how does the budger balance the cell pool",
    "what did the E2 replay benchmark measure",
    "where does the compactor hold the field lock",
    "how do era anchors persist the session trajectory",
]


def main() -> None:
    emb = OllamaEmbedder()
    st = ExemplarStore.load(FIELD)
    rows = [r for r in st.exemplars
            if getattr(r, "vec", None) is not None and r.action
            and not r.ephemeral]
    V = np.array([r.vec for r in rows], dtype=float)
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    print(f"field: {len(rows)} servable cells")

    # ---- phase costs, measured ----
    t0 = time.perf_counter()
    vecs = emb.embed(PROBES)
    embed_ms = (time.perf_counter() - t0) * 1000 / len(PROBES)

    qv = np.asarray(vecs[0], dtype=float)
    qv /= (np.linalg.norm(qv) + 1e-12)
    t0 = time.perf_counter()
    for _ in range(30):
        sims = Vn @ qv
    full_ms = (time.perf_counter() - t0) * 1000 / 30

    # ---- the prefetch lane's real cost: rank the cone ONCE per idle tick ----
    # cone = cells within 0.55 of the momentum direction; the momentum is a
    # REAL direction (the EMA of the probe queries' embeddings -- the same
    # shape the session:now pulse uses), not random (a random direction
    # aligns with nothing -- the 0-cells-in-cone exhibit)
    mom = np.zeros(Vn.shape[1], dtype=float)
    for v in vecs[:3]:
        mom += np.asarray(v, dtype=float)
    mom /= np.linalg.norm(mom)
    cone_align = Vn @ mom
    cone = np.where(cone_align >= 0.55)[0]
    cone_ms = (time.perf_counter() - t0) * 0  # computed below, honestly
    t0 = time.perf_counter()
    cone_align = Vn @ mom
    cone = np.where(cone_align >= 0.55)[0]
    cone_rank_ms = (time.perf_counter() - t0) * 1000

    # turn-time re-rank over the cone shortlist only
    if len(cone):
        sub = Vn[cone]
        t0 = time.perf_counter()
        for _ in range(30):
            sub_sims = sub @ np.asarray(vecs[0], dtype=float)
        rerank_ms = (time.perf_counter() - t0) * 1000 / 30
    else:
        rerank_ms = float("nan")
        sub_sims = np.array([])

    print(f"embed (cold, per query):       {embed_ms:7.1f} ms")
    print(f"full search ({len(rows)} cells):      {full_ms:7.2f} ms")
    print(f"cone rank at idle (once):      {cone_rank_ms:7.2f} ms  "
          f"({len(cone)} cells in cone)")
    print(f"turn-time re-rank (cone only): {rerank_ms:7.2f} ms")
    print()
    cold = embed_ms + full_ms
    warm = rerank_ms
    print(f"COLD turn (today):   {cold:7.1f} ms  (embed + search)")
    print(f"WARM turn (prefetch):{warm:7.2f} ms  (re-rank only, query "
          f"pre-embedded during idle)")
    print(f"latency win:         {cold / max(warm, 1e-9):.0f}x  "
          f"-- but the WIN IS THE EMBED REMOVAL: the search was already "
          f"{full_ms:.1f}ms")
    print()
    print("the honest read: prefetch does not buy recall LATENCY (the matmul")
    print("is ~1ms); it buys the EMBED REMOVAL for the 97.3% cone-hit turns")
    print("(E4) -- and pre-embedding during idle only helps if the operator's")
    print("next query is IN the cone AND phrased near a lived cell. The real")
    print("prefetch prize is not time: it is PRE-BUILDING the answer block")
    print("(recall + banner + reason-graph gather) so the turn starts warm.")


if __name__ == "__main__":
    main()
