#!/usr/bin/env python3
"""multi_lloyd -- the Lloyd split AS the cut (reshaped after P1).

P1 (multi_lloyd_p1.py) established free_energy(r) is MONOTONIC on a fixed field
(no interior optimum): with all-safe exemplars free_energy = effective_rank, and
the eff_rank of a rank-r projection tracks r. So the corridor is NOT a swept-r
interior peak; it is the [floor, cap] interval with monotonic viability, and the
Lloyd cut's job is to CAP r at K_max(h_mu) -- the residual tightens the capacity
ceiling. The residual (the environment's irreducible rate) positions the cell
along the corridor via the energy budget.

THE LLOYD CUT (a test-time construction over thermo_state; NO library edits):

  C_mu  <-> r       = kept effective rank (the stored structure; the cut)
  E     <-> F0      = free_energy(field)        (the base change-budget)
  h_mu  <-> e_off   = EXCESS off-manifold energy of the noisy probe stream over
                      the sigma=0 baseline, vs the field's FULL subspace. The
                      baseline (probe-field mismatch) is a FIXABLE model deficit,
                      NOT irreducible; the EXCESS (added noise) is the irreducible
                      rate. r-INDEPENDENT (a property of the stream).
  F     = F0 - ALPHA * e_off        (Landauer-analog: erasing the residual drains budget)
  K_max = BETA * F                  (Bekenstein-analog: kept structure <= energy-funded capacity)
  r*    = clamp(round(K_max), 1, eff_rank)   (keep as much as the cap allows -- monotonic
                                              viability -> greedy keep; floored at 1, ceiled)

ALPHA is AUTO-CALIBRATED so the sigma sweep spans BOTH walls (exhausts the budget
at the top sigma) -- a TOY CALIBRATION, flagged. The finding is the traversal
SHAPE (crystal -> corridor -> exhaustion), not ALPHA's value. BETA=1 (cap = available
energy). The coupling K_max = BETA(F0 - ALPHA e_off) is true BY CONSTRUCTION (higher
h_mu -> lower cap is algebra); it is the MECHANISM running, not a finding.

The two walls: low h_mu -> K_max high -> r* -> eff_rank -> gamma -> 0 (CRYSTAL:
over-keep, no null space). high h_mu -> F -> 0 -> K_max -> 0 -> r* -> 1 (budget
exhaustion -> under-keep). Intermediate h_mu -> r* in the middle (the corridor).

CONSTRUCTED vs EMERGENT (load-bearing):
  CONSTRUCTED (mechanism, not findings): the coupling, the budget floor (F<=0 at
    high sigma -- ALPHA is calibrated to make it so), the middle existing.
  EMERGENT (the real findings):
    (a) sig_crystal_wall -- at low h_mu, r*=eff_rank and gamma->0. gamma is the
        field's ACTUAL null-space width (var[eff_rank:] ~ 0 for a clean field) --
        a real spectral property, NOT the algebra. The capacity wall maps onto a
        real "no null space" signature.
    (b) sig_gas_wall (expected RED) -- at high h_mu the KEPT field is rank-1
        (under-keep), NOT a flat spectrum. The framework's GAS death (flat
        spectrum, no structure) needs the field to ABSORB the noise (a separate
        mechanism: `absorb`, per the endless environment caveat 7), not a cap on
        the kept fraction. The rate wall != the cap. This RED is the finding.

Lloyd-SHAPED, not Lloyd-identified: F0 is spectral (not joules), e_off is noise
energy (not bits/sec), ALPHA/BETA are invented toy analogues, there is NO R and
NO ER scaling (the lab has no radius). Two-wall structure only. The Landauer
erasure leg runs in the nonequilibrium regime where the info<->energy bridge is
unestablished (Rupe & Crutchfield 2024). Toy D=8 / fake-embedder calibrations.

No library edits; no commit. Runs with no Ollama / no key (fake embedder).
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.field import Exemplar, ExemplarStore
from membrane.variety import effective_rank, free_energy
from experiments.multi_cell import WORKLOAD, D  # noqa: E402
from experiments.multi_frontier import (  # noqa: E402
    EXT_BASE_KEYS, PROBES, _FakeEmbedder7, _base_of7,
)

_EMBED = _FakeEmbedder7()

# ---- toy calibrations (D=8 / fake-embedder; constructed analogues) --------------
SIGMA_IRR = [0.0, 0.10, 0.25, 0.40, 0.55, 0.70, 0.85]   # the residual h_mu sweep
BETA = 1.0       # capacity-per-energy (K_max = BETA * F; 1.0 -> cap = available energy)
N_DRAWS = 24     # denoise the h_mu measurement (mean off-manifold energy over draws)
# ALPHA is auto-calibrated in main (see docstring) -- a toy calibration to span both walls.


def _raw_field(texts: list[str]) -> ExemplarStore:
    s = ExemplarStore()
    for t in texts:
        v = np.asarray(_EMBED.embed([t])[0], dtype=float)
        s.append(Exemplar(situation=t, action=f"do-{_base_of7(t) or 'g'}",
                          outcome="", vec=v.tolist(), safe=True))
    return s


def _project_top_r(store: ExemplarStore, r: int) -> ExemplarStore:
    """The rank-r projection of the field onto its top-r principal directions (the
    'kept' field at kept fraction r). Re-unit-normalized."""
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


def _gamma(store: ExemplarStore, r: int) -> float:
    """The null-space width gamma = mean variance in the DROPPED directions
    (novelty's gamma). r = #kept directions; dropped = var[r:]. Small -> crystal
    (no null space); large -> much structure not kept (under-keep)."""
    V = store.vectors()
    n = len(store)
    if n < 2:
        return 0.0
    Y = V - V.mean(axis=0)
    _, S, _ = np.linalg.svd(Y, full_matrices=False)
    var = (S ** 2) / n
    r = max(1, min(r, len(var) - 1))      # always drop >= 1 (novelty's clamp)
    dropped = var[r:]
    return float(np.mean(dropped)) if dropped.size else 0.0


def _field_subspace(store: ExemplarStore):
    """The field's EXPERIENCED manifold = its top-k principal subspace
    (k = round(effective_rank)). The irreducible noise is OFF this subspace."""
    V = store.vectors()
    mu = V.mean(axis=0)
    Y = V - mu
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    k = max(1, min(round(effective_rank(store)), Vt.shape[0]))
    return mu, Vt[:k]                       # center, basis rows (k, D) orthonormal


def _off_manifold_energy(mu, basis, q) -> float:
    """||q - projection onto the field's subspace||^2 -- the off-manifold energy
    of one query. Independent of the kept fraction r."""
    x = np.asarray(q, dtype=float) - mu
    proj = basis.T @ (basis @ x)
    resid = x - proj
    return float(resid @ resid)


def _h_mu_raw(store: ExemplarStore, sigma: float, n: int = N_DRAWS) -> float:
    """Mean off-manifold energy of the noisy probe stream vs the field's FULL
    subspace (r-INDEPENDENT). At sigma=0 this is the probe-field MISMATCH (a
    fixable deficit); the EXCESS over sigma=0 is the irreducible rate (added noise)."""
    mu, basis = _field_subspace(store)
    rng = np.random.default_rng(20260826)
    es = []
    for b in EXT_BASE_KEYS:
        base = np.asarray(_EMBED.embed([PROBES[b]])[0], dtype=float)
        for _ in range(n):
            q = base + rng.normal(0.0, sigma, size=base.shape)
            qn = np.linalg.norm(q)
            q = q / qn if qn > 1e-12 else base
            es.append(_off_manifold_energy(mu, basis, q))
    return float(np.mean(es))


def _f(x: float) -> str:
    return f"{x:.2f}"


def main() -> None:
    print("multi_lloyd -- the Lloyd split AS the cut (cap r at K_max(h_mu); residual tightens capacity)")
    print(f"  BETA={BETA} (capacity/energy), N_DRAWS={N_DRAWS}; ALPHA auto-calibrated to span both walls\n")

    field = _raw_field(WORKLOAD)
    er0 = effective_rank(field)
    F0 = free_energy(field)
    er0_int = max(1, round(er0))
    print(f"  field: {len(field)} exemplars  D={D}  effective_rank={_f(er0)}  free_energy F0={_f(F0)}")
    print(f"  (all safe -> F0 = effective_rank; the base change-budget)")

    # h_mu = EXCESS off-manifold energy over the sigma=0 baseline (the irreducible part;
    # the baseline is a fixable probe-field deficit, not h_mu). ALPHA calibrated so the
    # max-sigma point exhausts the budget (the sweep spans both walls) -- toy calibration.
    h0 = _h_mu_raw(field, 0.0)
    h_max_excess = _h_mu_raw(field, SIGMA_IRR[-1]) - h0
    ALPHA = (F0 / h_max_excess) * 1.1 if h_max_excess > 1e-9 else 1.0
    print(f"  h_mu(0) baseline (probe-field mismatch) = {_f(h0)}  -- a fixable deficit, NOT irreducible")
    print(f"  h_mu = EXCESS over baseline (= the added-noise irreducible rate)")
    print(f"  ALPHA = {_f(ALPHA)} (auto-calibrated so sigma={SIGMA_IRR[-1]} exhausts the budget)\n")

    print(f"  {'sigma':>5} {'h_mu':>6} {'F':>6} {'K_max':>6} {'r*':>3} "
          f"{'eff_rank_kept':>13} {'free_energy':>12} {'gamma':>7}  wall")
    rows = []
    for sigma in SIGMA_IRR:
        h = _h_mu_raw(field, sigma) - h0          # the irreducible rate (excess)
        F = F0 - ALPHA * h
        Kmax = BETA * F
        rstar = int(max(1, min(er0_int, round(Kmax))))
        kept = _project_top_r(field, rstar)
        fe = free_energy(kept)
        g = _gamma(field, rstar)
        if F <= 0:
            wall = "BUDGET-EXH"
        elif rstar >= er0_int:
            wall = "CRYSTAL-cap"
        elif rstar <= 1:
            wall = "floor"
        else:
            wall = "corridor"
        rows.append(dict(sigma=sigma, h=h, F=F, Kmax=Kmax, rstar=rstar, fe=fe, g=g, wall=wall))
        print(f"  {sigma:>5.2f} {_f(h):>6} {_f(F):>6} {_f(Kmax):>6} {rstar:>3} "
              f"{_f(effective_rank(kept)):>13} {_f(fe):>12} {_f(g):>7}  {wall}")

    # ---- signatures (honest: constructed mechanism vs emergent findings) ----
    print("\n=== signatures ===")
    kmax_seq = [r["Kmax"] for r in rows]
    rstar_seq = [r["rstar"] for r in rows]
    sig_coupling = (all(kmax_seq[i + 1] <= kmax_seq[i] + 1e-9 for i in range(len(kmax_seq) - 1))
                    and all(rstar_seq[i + 1] <= rstar_seq[i] for i in range(len(rstar_seq) - 1)))
    print(f"  sig_coupling (K_max & r* fall with sigma):      {'GREEN' if sig_coupling else 'RED'}"
          f"   [mechanism] K_max={[ _f(x) for x in kmax_seq ]}  r*={rstar_seq}")

    low = rows[0]
    gmax = max(r["g"] for r in rows)
    sig_crystal = (low["rstar"] >= er0_int) and (low["g"] < 0.1 * gmax + 1e-12)
    print(f"  sig_crystal_wall (low sigma: r*=eff_rank, gamma->0): {'GREEN' if sig_crystal else 'RED'}"
          f"   [EMERGENT] r*={low['rstar']}/{er0_int}  gamma={_f(low['g'])}  (real null-space property)")

    high = rows[-1]
    sig_budget = (high["F"] <= 0) and (high["rstar"] <= 1)
    print(f"  sig_budget_floor (high sigma: F<=0, r* floored):    {'GREEN' if sig_budget else 'RED'}"
          f"   [calibrated] F={_f(high['F'])}  r*={high['rstar']}  (ALPHA set to make it so)")

    mid = [r for r in rows if 1 < r["rstar"] < er0_int and r["F"] > 0]
    sig_middle = len(mid) > 0
    print(f"  sig_viable_middle (1 < r* < eff_rank, F>0):        {'GREEN' if sig_middle else 'RED'}"
          f"   [mechanism] {len(mid)} corridor pt(s): r*={[r['rstar'] for r in mid]}")

    # GAS wall (honest, expected RED): does the high-sigma KEPT field go FLAT (= gas death),
    # or PEAKED rank-1 (= under-keep)? A fixed clean field + cap yields rank-1, NOT flat.
    high_kept = _project_top_r(field, high["rstar"])
    hke = effective_rank(high_kept)
    if hke >= 0.9 * D:
        gas_label = "FLAT ~ D (gas death?)"
    elif hke <= 1.2:
        gas_label = "PEAKED rank-1 (under-keep, NOT flat=gas)"
    else:
        gas_label = f"moderate eff_rank={_f(hke)} (neither flat=gas nor rank-1)"
    sig_gas_wall = (high["F"] <= 0) and (hke >= 0.9 * D)          # flat -> eff_rank near D
    print(f"  sig_gas_wall (high sigma: KEPT field FLAT = gas death?): "
          f"{'GREEN' if sig_gas_wall else 'RED (expected)'}")
    print(f"    [EMERGENT] high-sigma kept eff_rank={_f(hke)} / D={D} -> {gas_label}")
    print(f"    -> the cap produces UNDER-KEEP at the rate wall, not the flat-spectrum gas death.")
    print(f"       The gas death needs the field to ABSORB the noise (a separate mechanism), not a cap.")

    print("\n=== verdict ===")
    mechanism = sig_coupling and sig_budget and sig_middle
    print(f"  MECHANISM (constructed: coupling + budget floor + middle):  {'runs' if mechanism else 'FAILS'}")
    print(f"  FINDING 1 -- crystal/capacity wall (gamma->0 at over-keep): {'GREEN (real)' if sig_crystal else 'RED'}")
    print(f"  FINDING 2 -- gas/rate wall (flat-spectrum gas death):       {'GREEN' if sig_gas_wall else 'RED -- the rate wall != the cap'}")
    if sig_crystal and mechanism and not sig_gas_wall:
        print("\n  OVERALL: the Lloyd cut, as a CAP, exhibits the residual-tightens-capacity coupling")
        print("  (constructed mechanism) AND one emergent wall -- the CRYSTAL/capacity wall: at low")
        print("  residual r*=eff_rank and the field's null-space gamma->0 (a real spectral property,")
        print("  not the algebra). The residual traverses crystal -> corridor -> budget exhaustion. The")
        print("  second wall -- the framework's flat-spectrum GAS death -- is NOT reproduced: the cap")
        print("  yields UNDER-KEEP (rank-1) at the rate wall, not a flat spectrum. The gas death needs")
        print("  ABSORPTION (the field taking up the noisy stream), a separate mechanism not in this cut.")
        print("  Net: the Lloyd split's CAPACITY wall is honest (maps to a real null-space signature);")
        print("  its RATE wall is a different mechanism (absorption), not the cap. Half the two-wall")
        print("  structure is emergent; the other half needs Mode B (absorbing field).")
    else:
        print("\n  OVERALL: RED (see above)")
    print("\n  HONEST CAVEAT: a test of the Lloyd-SHAPED reading, not a proof. The coupling is")
    print("  constructed (K_max = BETA(F0 - ALPHA h_mu) is algebra; ALPHA is calibrated to span). The")
    print("  emergent findings are (1) the crystal wall's gamma->0 (real) and (2) the gas wall's")
    print("  non-reproduction (the cap gives rank-1 under-keep, not flat). F0 is spectral (not joules),")
    print("  h_mu is noise energy (not bits/sec), ALPHA/BETA invented, NO R / NO ER scaling. The")
    print("  Landauer erasure leg runs nonequilibrium (caveat 3). Toy D=8 calibrations.")
    print("\nartifacts: in-memory only (nothing written to the repo)")


if __name__ == "__main__":
    main()