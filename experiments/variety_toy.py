"""Toy: show variety measures discriminate low/high-variety stores, and that
novelty (off-manifold) is the 'not sure' signal that calibrates the gate.

Three demonstrations:

  1. The store's OWN variety (effective_rank, spread) discriminates low vs high.
     -> this is the "measure variety" deliverable: the field can report how much
     of the situation-space it has actually experienced.
  2. The SAME off-query is more novel to the low-variety field than the high one.
     -> variety is what makes the novelty signal sharp.
  3. A query can be HIGH cosine (sim says "act") yet HIGH novelty (off-manifold).
     -> the gate escalates on novelty despite high sim. This is the acquisition
     gap plain cosine misses, and the whole reason variety measurement exists.

Run:  python experiments/variety_toy.py   (numpy only; no Ollama needed)
"""

from __future__ import annotations

import numpy as np

from cortex.field import Exemplar, ExemplarStore
from cortex.variety import effective_rank, spread, novelty
from cortex.gate import decide

# n > D so there are no literally-zero-variance directions (real dense embeddings
# are like this; pure synthetic 1-D data is not, which would make gamma collapse).
D = 8
N = 64
NOISE = 0.10                       # isotropic noise floor -> the null space has a real gamma
rng = np.random.default_rng(0)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / (np.linalg.norm(v) + 1e-12)


def make_store(mode: str, action: str = "actA") -> ExemplarStore:
    """low: exemplars vary along ONE direction + a noise floor (1-D manifold).
    high: exemplars spread isotropically (fill the D-sphere)."""
    store = ExemplarStore()
    if mode == "low":
        center = rng.standard_normal(D)      # a fixed base point (large norm)
        d = rng.standard_normal(D)           # the single direction to vary along
        for _ in range(N):
            t = rng.standard_normal()        # vary only the scalar t -> signal along d
            v = _unit(center + 0.5 * t * d + NOISE * rng.standard_normal(D))
            store.append(Exemplar("1-D situation", action, "ok", v.tolist(), safe=True))
    else:
        for _ in range(N):
            v = _unit(rng.standard_normal(D))
            store.append(Exemplar("spread situation", action, "ok", v.tolist(), safe=True))
    return store


def _manifold_directions(store: ExemplarStore) -> tuple[np.ndarray, np.ndarray]:
    """Return (mu, first_principal_direction) of a store, for constructing probes."""
    V = store.vectors()
    mu = V.mean(axis=0)
    Y = V - mu
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    return mu, Vt[0]


def main() -> None:
    low = make_store("low")
    high = make_store("high")

    print("== 1. variety of the two stores ==")
    print(f"low-variety  store: effective_rank={effective_rank(low):5.2f}  spread={spread(low):.3f}")
    print(f"high-variety store: effective_rank={effective_rank(high):5.2f}  spread={spread(high):.3f}")
    print("(low ~1; high ~D. The field can report its own experienced variety.)\n")

    off = _unit(rng.standard_normal(D))     # a random direction, off both manifolds

    print("== 2. the SAME off-query, novelty vs each store ==")
    print(f"novelty(off query, low store) ={novelty(off, low):6.2f}")
    print(f"novelty(off query, high store)={novelty(off, high):6.2f}")
    print("(higher for the less-experienced field -> variety sharpens novelty.)\n")

    print("== 3. high cosine but off-manifold (the gap plain cosine misses) ==")
    mu, line = _manifold_directions(low)
    perp = rng.standard_normal(D)
    perp -= line * (perp @ line)            # orthogonal to the 1-D manifold
    perp = _unit(perp)
    # near the centroid (high cosine to neighbors) but pushed off the line:
    gap = _unit(mu + 2.0 * perp)
    sim_gap = float(np.dot(_unit(gap), _unit(mu)))
    nov_gap = novelty(gap, low)
    print(f"query sim to nearest ~{sim_gap:.2f} (high -> cosine says 'act')")
    print(f"query novelty            {nov_gap:.2f} (high -> off the manifold)")
    print(f"gate -> {decide(gap, low)}")
    print("(the gate escalates on novelty despite high sim -- variety measurement\n"
          " is what catches what cosine cannot: near, but in a direction unseen.)\n")

    print("== sanity: a query ON the manifold ==")
    on = _unit(mu + 0.5 * line * rng.standard_normal())
    print(f"gate (on-manifold, low store) -> {decide(on, low)}")


if __name__ == "__main__":
    main()