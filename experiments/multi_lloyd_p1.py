#!/usr/bin/env python3
"""P1 standalone check -- is free_energy(r) PEAKED in the kept fraction, or monotonic?

The framework (`the endless environment.md` §1) claims free_energy is "maximized in
the kept fraction, zero at both deaths." Before building the Lloyd-cut experiment
around an interior optimum, this checks the claim directly: sweep the kept fraction
r on a FIXED spread-spectrum field (the 7-base toy), build the rank-r projection
of the field (the "kept" field at fraction r -- the same top-r principal subspace
`novelty`'s KEEP_FRACTION retains), and measure free_energy at each r.

Held as a check of the framework's OWN claim, not a proof. No library edits; no
commit. Toy D=8 / fake-embedder regime.

The risk flagged in the design: `free_energy = (safe_frac - 0.25*unsafe_frac) *
effective_rank`, and with all exemplars safe, `free_energy = effective_rank`. The
eff_rank of a rank-r projection of a spread field tracks r -- so free_energy(r) is
EXPECTED monotonic, with no interior peak. If so, the "corridor" is NOT a swept-r
interior optimum; it is the [gas floor, crystal cap] interval with monotonic
viability, and the Lloyd cut's job is to CAP r (residual-tightens-capacity), not
to find an interior peak. This run settles which.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.field import Exemplar, ExemplarStore
from membrane.variety import effective_rank, free_energy, spectrum_entropy
from experiments.multi_cell import BASE_KEYS, WORKLOAD, D  # noqa: E402
from experiments.multi_frontier import _FakeEmbedder7, _base_of7  # noqa: E402

_EMBED = _FakeEmbedder7()


def _raw_field(texts: list[str]) -> ExemplarStore:
    s = ExemplarStore()
    for t in texts:
        v = np.asarray(_EMBED.embed([t])[0], dtype=float)
        s.append(Exemplar(situation=t, action=f"do-{_base_of7(t) or 'g'}",
                          outcome="", vec=v.tolist(), safe=True))
    return s


def _project_top_r(store: ExemplarStore, r: int) -> ExemplarStore:
    """The rank-r projection of the field onto its top-r principal directions --
    the 'kept' field at kept fraction r (the subspace `novelty`'s KEEP_FRACTION
    retains). Exemplars projected then re-unit-normalized."""
    V = store.vectors()
    mu = V.mean(axis=0)
    Y = V - mu
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    r = max(1, min(r, Vt.shape[0]))
    basis = Vt[:r].T                       # (D, r)
    kept = ExemplarStore()
    for ex in store.exemplars:
        v = np.asarray(ex.vec, dtype=float)
        vk = mu + basis @ (basis.T @ (v - mu))
        n = np.linalg.norm(vk)
        vk = vk / n if n > 1e-12 else v
        kept.append(Exemplar(situation=ex.situation, action=ex.action,
                             outcome=ex.outcome, vec=vk.tolist(), safe=ex.safe))
    return kept


def main() -> None:
    field = _raw_field(WORKLOAD)
    er0 = effective_rank(field)
    fe0 = free_energy(field)
    print("P1 -- free_energy(r) sweep on a FIXED spread-spectrum field (the 7-base toy)")
    print(f"  field: {len(field)} exemplars  D={D}  effective_rank={er0:.2f}  free_energy={fe0:.2f}")
    print(f"  safe_frac=1.0 -> free_energy = effective_rank; so free_energy(r) tracks the")
    print(f"  eff_rank of the rank-r projection (the kept subspace).\n")
    print(f"  {'r':>3} {'eff_rank':>9} {'free_energy':>12} {'spec_entropy':>12}")

    rs = sorted(set(range(1, int(round(er0)) + 1)))
    vals = []
    for r in rs:
        kf = _project_top_r(field, r)
        er = effective_rank(kf)
        fe = free_energy(kf)
        se = spectrum_entropy(kf)
        vals.append((r, fe, er))
        print(f"  {r:>3} {er:>9.2f} {fe:>12.2f} {se:>12.3f}")

    fes = [fe for _, fe, _ in vals]
    argmax_r = vals[int(np.argmax(fes))][0]
    interior = argmax_r != rs[0] and argmax_r != rs[-1]
    monotonic = all(fes[i + 1] >= fes[i] - 1e-9 for i in range(len(fes) - 1))
    print(f"\n  argmax free_energy at r={argmax_r} (sweep {rs[0]}..{rs[-1]})")
    print(f"  interior peak? {'YES -- corridor reproduced as a swept-r optimum' if interior else 'NO -- peak at an endpoint'}")
    print(f"  monotonic non-decreasing? {monotonic}")

    print("\n  HONEST READ:")
    if not interior:
        print(f"  free_energy(r) is monotonic (rises with r) -- NO interior optimum. The framework's")
        print(f"  'maximized in the kept fraction' does NOT reproduce as a swept-r peak on a fixed field:")
        print(f"  with all-safe exemplars free_energy = effective_rank, and eff_rank of a rank-r")
        print(f"  projection tracks r. The corridor is therefore NOT a swept-r interior peak; it is the")
        print(f"  [gas floor, crystal cap] interval with MONOTONIC viability (more keep = more free_energy,")
        print(f"  until the cap). The Lloyd cut's job becomes: CAP r at K_max(h_mu) -- the residual tightens")
        print(f"  the capacity ceiling -- not 'find an interior optimum.' Reshapes P2/P3 accordingly.")
    else:
        print(f"  free_energy(r) HAS an interior peak at r={argmax_r} -- the corridor reproduces as a")
        print(f"  swept-r optimum. The Lloyd cut can be built around an emergent interior balance point.")
    print("\nartifacts: in-memory only (nothing written to the repo)")


if __name__ == "__main__":
    main()