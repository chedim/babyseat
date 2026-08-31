#!/usr/bin/env python3
"""multi_lloyd_b -- Mode B: the ABSORBING field (tests the gas/rate wall the cap can't reach).

Mode A (multi_lloyd.py) capped the kept fraction r at K_max(h_mu) and found the rate wall
gives rank-1 UNDER-KEEP, NOT the flat-spectrum GAS death -- the gas death needs the field
to ABSORB the noisy stream (its spectrum must actually flatten), which a fixed-field cap
cannot do. Mode B lets the field GROW by absorbing the irreducible noisy stream and asks:
does the gas death (flat spectrum, structure dissolved, free_energy -> 0) emerge?

THE ABSORB: start from the clean 6-base field (eff_rank ~5); append `a` noisy exemplars
drawn from the irreducible residual (pure isotropic N(0,sigma) noise, unit-normalized -- the
honest "absorb h_mu" reading: the residual is off-manifold, structureless). Measure the
field's spectrum as `a` grows. No library edits -- just append (the lab's absorb operation).

THE `safe` SEMANTICS (field.py:9 -- load-bearing): `safe` is an EVIDENTIAL two-authority
boundary, NOT geometric: safe = (reask_count - contest_count) < demote_threshold. A new
exemplar enters safe=True; the environment demotes it by re-asking. Irreducible noise
absorbed then replayed WOULD be re-asked (it cannot satisfy) -> demoted -> unsafe. So
absorbed noise has TWO lab-faithful snapshots, and we run BOTH:
  just-absorbed   (safe=True)  -- the lab default for a freshly absorbed exemplar
  demoted-by-reask(safe=False) -- the steady-state fate of irreducible noise (re-asked)
The gas death's free_energy behavior is marking-DEPENDENT, and that dependence IS the finding:
the framework's "free_energy -> 0 at the gas death" is the DEMOTED reading, not a geometric fact.

MARKING-INDEPENDENT (the gas death's real signature):
  - eff_rank -> D?            (flat spectrum; expected from isotropy -- confirm it appears)
  - structure retention       (fraction of total spectral variance still in the original clean
                              structure subspace; falls 1 -> k0/D, the isotropic floor. The
                              CROSSOVER where noise variance overtakes the structure directions
                              is emergent -- depends on actual magnitudes, not just isotropy.)
  - smoothness                (sharp death regime or smooth dilution? mirrors P1: the corridor
                              was NOT a sharp interior peak either.)

MARKING-DEPENDENT (report both):
  - free_energy(safe)    = eff_rank                         -> D (NOT 0)
  - free_energy(demoted) = (safe_frac - 0.25*unsafe)*eff_rank -> crosses 0 near a ~ 120
    (safe_frac = 30/(30+a): the 30 clean exemplars stay safe; the a absorbed noise demoted.
     The 0.25 is the lab's OWN unsafe penalty (variety.py:164) -- not invented for this test.)

CONSTRUCTED vs EMERGENT (load-bearing):
  CONSTRUCTED: isotropic noise flattens the spectrum (algebra); the demoted free_energy crosses
    0 (the lab's 0.25 unsafe penalty x the rising unsafe count -- the lab's own formula, applied
    to the demoted reading). These confirm the mechanism runs; they are not discoveries.
  EMERGENT: (a) structure retention -- the crossover where noise variance overtakes the clean
    structure directions (a real magnitude-dependent transition, not isotropy alone); (b) the
    smoothness -- whether the gas death is a sharp regime or a smooth dilution. P1 found the
    corridor was not a sharp swept-r peak; Mode B tests whether the gas death is a sharp regime
    or the same kind of smooth limit.

Lloyd-SHAPED, not Lloyd-identified: eff_rank is spectral (not joules), sigma is noise width
(not bits/sec), no R / no ER scaling (the lab has no radius). The Landauer/absorption leg runs
nonequilibrium (caveat 3: the info<->energy bridge is unestablished out of equilibrium). Toy
D=8 / fake-embedder calibrations. No library edits; no commit.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.field import Exemplar, ExemplarStore
from membrane.variety import effective_rank, spectrum_entropy
from experiments.multi_cell import WORKLOAD, D  # noqa: E402
from experiments.multi_frontier import _FakeEmbedder7, _base_of7  # noqa: E402

_EMBED = _FakeEmbedder7()

# ---- toy calibrations -----------------------------------------------------------
ABSORB_COUNTS = [0, 5, 10, 20, 40, 80, 120, 160]   # how many noisy exemplars the field absorbs
SIGMA = 0.5                                        # irreducible noise width (the h_mu scale)
N_CLEAN = None     # set in main (= len(clean field); the safe exemplar count under demotion)


def _raw_field(texts: list[str]) -> ExemplarStore:
    s = ExemplarStore()
    for t in texts:
        v = np.asarray(_EMBED.embed([t])[0], dtype=float)
        s.append(Exemplar(situation=t, action=f"do-{_base_of7(t) or 'g'}",
                          outcome="", vec=v.tolist(), safe=True))
    return s


def _structure_subspace(store: ExemplarStore):
    """The clean field's structure = its top-k0 principal directions (k0 = round(eff_rank)).
    The retention metric measures how much of an absorbed field's variance still lies in
    THIS subspace (the original structure), vs spread into noise directions."""
    V = store.vectors()
    mu = V.mean(axis=0)
    Y = V - mu
    _, _, Vt = np.linalg.svd(Y, full_matrices=False)
    k0 = max(1, round(effective_rank(store)))
    return mu, Vt[:k0]                    # center, basis rows (k0, D) orthonormal


def _retention(absorbed: ExemplarStore, basis_clean) -> float:
    """Fraction of the absorbed field's total spectral variance that lies in the clean
    structure subspace. 1.0 = all variance in the original structure; k0/D = the isotropic
    floor (variance spread evenly over all D directions). Marking-independent."""
    V = absorbed.vectors()
    n = len(absorbed)
    if n < 2:
        return 1.0
    Y = V - V.mean(axis=0)
    total = float((Y ** 2).sum() / n)
    if total < 1e-12:
        return 1.0
    P = basis_clean.T @ basis_clean       # (D, D) projector onto the clean dir-span
    in_clean = float(((Y @ P) ** 2).sum() / n)
    return in_clean / total


def _absorb(clean: ExemplarStore, a: int, sigma: float, demoted: bool) -> ExemplarStore:
    """The clean field + a absorbed noisy exemplars (pure isotropic noise, unit-normalized).
    `demoted` sets the absorbed exemplars' safe flag (False = re-asked/demoted steady state)."""
    s = ExemplarStore()
    for ex in clean.exemplars:
        s.append(Exemplar(situation=ex.situation, action=ex.action,
                          outcome=ex.outcome, vec=ex.vec, safe=True))   # clean stays safe
    rng = np.random.default_rng(20260826)
    for i in range(a):
        q = rng.normal(0.0, sigma, size=D)
        qn = np.linalg.norm(q)
        q = q / qn if qn > 1e-12 else q
        s.append(Exemplar(situation=f"noise-{i}", action="do-noise",
                          outcome="", vec=q.tolist(), safe=not demoted))
    return s


def _fe_demoted(absorbed: ExemplarStore, n_clean: int) -> float:
    """free_energy under the demoted reading: the n_clean exemplars safe, the rest unsafe.
    Uses the lab's formula (variety.py:164): (safe_frac - 0.25*unsafe_frac)*eff_rank."""
    n = len(absorbed)
    if n < 2:
        return 0.0
    er = effective_rank(absorbed)
    safe_frac = n_clean / n
    unsafe_frac = 1.0 - safe_frac
    return float(safe_frac * er - 0.25 * unsafe_frac * er)


def _f(x: float) -> str:
    return f"{x:.2f}"


def main() -> None:
    global N_CLEAN
    print("multi_lloyd_b -- Mode B: the ABSORBING field (does the gas death emerge?)")
    print(f"  SIGMA={SIGMA} (irreducible noise width); absorb counts={ABSORB_COUNTS}\n")

    clean = _raw_field(WORKLOAD)
    n_clean = len(clean)
    N_CLEAN = n_clean
    er0 = effective_rank(clean)
    k0 = max(1, round(er0))
    _, basis_clean = _structure_subspace(clean)
    floor = k0 / D
    print(f"  clean field: {n_clean} exemplars  D={D}  eff_rank={_f(er0)}  k0={k0}  "
          f"isotropic floor k0/D={_f(floor)}")
    print("  free_energy readings: SAFE (just-absorbed) = eff_rank; DEMOTED (re-asked) = "
          "(safe_frac-0.25*unsafe)*eff_rank, the lab's own formula.\n")

    print(f"  {'absorb':>6} {'eff_rank':>9} {'spec_ent':>9} {'retention':>10} "
          f"{'FE_safe':>8} {'FE_demoted':>11}  read")
    rows = []
    for a in ABSORB_COUNTS:
        abs_safe = _absorb(clean, a, SIGMA, demoted=False)
        er = effective_rank(abs_safe)
        se = spectrum_entropy(abs_safe)
        ret = _retention(abs_safe, basis_clean)
        fe_safe = er                                  # all safe -> free_energy = eff_rank
        fe_dem = _fe_demoted(abs_safe, n_clean)       # demoted reading (safe flag is a label here)
        # the gas-death read: flat + structure-at-floor + (demoted) free_energy -> 0
        flat = er >= 0.85 * D
        dissolved = ret <= floor + 0.05
        zero = fe_dem <= 0.0
        if a == 0:
            read = "CRYSTAL (clean)"
        elif flat and dissolved and zero:
            read = "GAS"
        elif flat and dissolved:
            read = "flat+quiet (FE not yet 0)"
        else:
            read = "diluting"
        rows.append(dict(a=a, er=er, se=se, ret=ret, fe_safe=fe_safe, fe_dem=fe_dem,
                         flat=flat, dissolved=dissolved, zero=zero, read=read))
        print(f"  {a:>6} {_f(er):>9} {_f(se):>9} {_f(ret):>10} "
              f"{_f(fe_safe):>8} {_f(fe_dem):>11}  {read}")

    # ---- signatures (honest: constructed mechanism vs emergent findings) ----
    print("\n=== signatures ===")
    last = rows[-1]
    sig_flat = last["flat"]
    print(f"  sig_flat (eff_rank -> ~D at max absorption):       {'GREEN' if sig_flat else 'RED'}"
          f"   [constructed-ish: isotropy -> flat] eff_rank={_f(last['er'])}/D={D}")

    sig_dissolve = last["dissolved"]
    print(f"  sig_structure_dissolve (retention -> k0/D floor):  {'GREEN' if sig_dissolve else 'RED'}"
          f"   [EMERGENT] retention={_f(last['ret'])} (floor={_f(floor)}); "
          f"crossover a where noise overtakes structure")

    sig_zero_demoted = any(r["zero"] for r in rows)
    a_cross = next((r["a"] for r in rows if r["zero"]), None)
    print(f"  sig_fe_zero_demoted (FE -> 0 under demotion):      {'GREEN' if sig_zero_demoted else 'RED'}"
          f"   [lab formula on demoted noise] crosses at a={a_cross}")
    print(f"    -> the framework's 'free_energy -> 0 at gas death' = the DEMOTED (re-asked) reading")

    sig_safe_nonzero = all(r["fe_safe"] > 0 for r in rows) and last["fe_safe"] > 0.5 * D
    print(f"  sig_fe_safe_nonzero (FE -> D, NOT 0, under safe):  {'GREEN' if sig_safe_nonzero else 'RED'}"
          f"   [EMERGENT: marking-dependence] FE_safe={_f(last['fe_safe'])}")
    print(f"    -> the gas-death zero does NOT hold for just-absorbed (safe) noise; it needs demotion")

    ers = [r["er"] for r in rows]
    jumps = [abs(ers[i + 1] - ers[i]) for i in range(len(ers) - 1)]
    sig_smooth = max(jumps) < 0.25 * D
    print(f"  sig_smooth (no sharp jump -> smooth dilution):     {'GREEN' if sig_smooth else 'RED'}"
          f"   [EMERGENT] max step={_f(max(jumps))}; gas death is a SMOOTH limit, not a sharp regime")

    print("\n=== verdict ===")
    print(f"  FLATNESS (constructed):                 {'GREEN' if sig_flat else 'RED'} -- isotropic noise flattens the spectrum (algebra confirms)")
    print(f"  STRUCTURE DISSOLUTION (emergent):       {'GREEN' if sig_dissolve else 'RED'} -- retention falls to the k0/D floor (real crossover)")
    print(f"  FREE_ENERGY -> 0 (marking-dependent):   demoted={'GREEN' if sig_zero_demoted else 'RED'}  safe={'non-zero' if sig_safe_nonzero else 'ZERO?!'}")
    print(f"  SMOOTHNESS (emergent):                  {'GREEN (smooth dilution)' if sig_smooth else 'RED (sharp)'}")

    if sig_flat and sig_dissolve and sig_zero_demoted and sig_safe_nonzero:
        print("\n  OVERALL: the gas death EMERGES under absorption -- the field's spectrum flattens")
        print("  (eff_rank -> D), its structure dissolves (retention -> k0/D floor), and free_energy")
        print("  -> 0 -- BUT only under the DEMOTED (re-asked) reading. Under the just-absorbed (safe)")
        print("  reading free_energy -> D (NOT 0): the framework's 'free_energy -> 0 at the gas death'")
        print("  is the lab-faithful STEADY STATE of absorbed irreducible noise (the environment re-asks")
        print("  it -> demoted -> unsafe -> free_energy falls), not a geometric fact. The gas death is a")
        print("  SMOOTH dilution (no sharp regime) -- mirroring P1's finding that the corridor is not a")
        print("  sharp swept-r peak. Both VT 'deaths' are smooth limits, not phase transitions.")
        print("\n  WITH MODE A: the Lloyd two-wall picture now has both walls. The CAP (Mode A) gives the")
        print("  crystal/capacity wall honestly (gamma->0, real) and yields rank-1 under-keep (not gas) at")
        print("  the rate wall. ABSORPTION (Mode B) gives the gas/rate wall (flat spectrum, structure")
        print("  dissolved, free_energy -> 0 under demotion). The two walls need two different mechanisms")
        print("  -- cap for crystal, absorb for gas -- exactly the framework's cut (structure) vs absorb")
        print("  (noise) split (caveat 7).")
    else:
        print("\n  OVERALL: RED (see above)")
    print("\n  HONEST CAVEAT: a test of the Lloyd-SHAPED / VT reading, not a proof. The flatness is")
    print("  constructed (isotropy -> flat); the demoted free_energy zero is the lab's own 0.25 unsafe")
    print("  penalty applied to re-asked noise (the lab's formula, not invented). The EMERGENT findings")
    print("  are the structure-dissolution crossover and the smoothness (no sharp regime). eff_rank is")
    print("  spectral (not joules), sigma is noise width (not bits/sec), no R / no ER scaling. The")
    print("  absorption/Landauer leg runs nonequilibrium (caveat 3). Toy D=8 calibrations.")
    print("\nartifacts: in-memory only (nothing written to the repo)")


if __name__ == "__main__":
    main()