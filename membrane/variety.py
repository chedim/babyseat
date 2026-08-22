"""Variety measures for the exemplar store, and per-query novelty.

Variety = how much of the situation-space the field has actually experienced.
It calibrates the confidence gate: a store with low variety cannot be *sure*
about a query off its experienced manifold, no matter the nearest cosine.

This is the covariance / null-space work from rosetta-check, in its correct home.
In the linter it was a solution hunting for a problem; here the confidence gate
genuinely needs a variety-calibrated novelty signal, so the math earns its keep.

Notes:
  - Direct DxD covariance / SVD is used here (small D in the toy). At D=1024 (real
    mxbai embeddings) you'd form the small NxN Gram matrix instead of the 1024x1024
    covariance -- exactly the Gram trick rosetta-check's PCA.java already uses. The
    measures below are written on the centered data Y so the Gram swap is a one-line
    change later.
  - novelty deliberately keeps FEWER principal directions than the effective rank
    (KEEP_FRACTION below), so the residual null space has a real, nonzero scale
    gamma. This under-keeping IS the "null space is the asset" choice: it is what
    lets novelty catch a query that is near the centroid (high cosine) but OFF the
    manifold. Choosing KEEP_FRACTION is the calibration knob that is this project's
    open problem -- it is exposed, not hidden.
"""

from __future__ import annotations

import numpy as np

from .field import ExemplarStore

# Keep this fraction of the effective rank as the "experienced manifold"; the
# rest forms the null space whose variance (gamma) scales novelty. <1 so there
# is always a real null space (the asset). Tuning this is the open calibration
# problem -- see README "Variety calibration".
KEEP_FRACTION = 0.8


def _entropy_rank(var: np.ndarray, eps: float = 1e-12) -> float:
    """Effective rank = exp(Shannon entropy of the normalized variance spectrum.

    One dominant direction -> spectrum is one big spike -> entropy ~0 -> rank ~1.
    Variance spread evenly across many directions -> flat spectrum -> rank ~ D.
    """
    var = np.clip(var, 0.0, None)
    s = var.sum()
    if s < eps:
        return 0.0
    p = var[var > eps] / s
    return float(np.exp(-np.sum(p * np.log(p))))


def effective_rank(store: ExemplarStore, eps: float = 1e-9) -> float:
    """Entropy of the covariance eigenvalue spectrum (a.k.a. effective dimensionality).

    Intuition: if every exemplar points the same way, all variance is along one
    axis -> the spectrum is one big eigenvalue and the rest near zero -> entropy
    ~0 -> rank ~1 (low variety). If exemplars spread evenly across many axes, the
    spectrum is flat -> entropy ~ D (high variety). Low effective rank -> low
    variety.

    This generalizes rosetta-check's fixed-``rank`` lesson: instead of picking a
    magic k, we measure how many directions the field has actually seen.
    """
    return _effective_rank_vectors(store.vectors(), eps)


def _effective_rank_vectors(V: np.ndarray, eps: float = 1e-9) -> float:
    """``effective_rank`` on a raw (N, D) array instead of a store (shared core)."""
    if V.shape[0] < 2 or V.shape[1] == 0:
        return 0.0
    Y = V - V.mean(axis=0)
    # SVD (not DxD covariance) so this scales to D=1024 real embeddings without
    # forming a 1024x1024 matrix. var_i = S_i^2/n are the covariance eigenvalues;
    # entropy is scale-invariant under normalization, so this == eigvalsh(C).
    _, S, _ = np.linalg.svd(Y, full_matrices=False)
    var = (S ** 2) / V.shape[0]
    return _entropy_rank(var, eps)


def effective_rank_with(store: ExemplarStore, vec: np.ndarray,
                        eps: float = 1e-9) -> float:
    """What ``effective_rank`` WOULD be if ``vec`` were appended -- without mutating
    the store. Used by the variety-learn-gate: only append an exemplar if it grows
    the field's effective rank (adds variety, not just count). Cheap path is a 2x
    SVD per learn, so the gate is OFF by default."""
    V = store.vectors()
    q = np.asarray(vec, dtype=float).reshape(1, -1)
    if V.shape[1] == 0:
        aug = q
    elif V.shape[1] != q.shape[1]:
        # dimension mismatch (shouldn't happen with one embedder) -- be safe
        return _effective_rank_vectors(V, eps)
    else:
        aug = np.vstack([V, q])
    return _effective_rank_vectors(aug, eps)


def spectrum_entropy(store: ExemplarStore, eps: float = 1e-9) -> float:
    """The raw Shannon entropy of the variance spectrum = ``log(effective_rank)``.

    ``effective_rank`` is ``exp(entropy)``, i.e. it lives on the *count* axis
    (1..D, "how many directions"); ``spectrum_entropy`` lives on the *bits* axis
    (0..log D). Both describe the same flatness-of-spectrum; surfacing the log
    form makes the thermodynamic reading direct: this IS the field's entropy.
    Returns 0.0 for <2 exemplars (no spectrum to speak of)."""
    r = effective_rank(store, eps)
    return float(np.log(r)) if r > 0 else 0.0


def free_energy(store: ExemplarStore, eps: float = 1e-9) -> float:
    """The field's thermodynamic VALUE -- one number.

    Reliable, non-redundant knowledge the field holds, MINUS a disorder tax:

        F = (safe fraction) * effective_rank  -  disorder

    where ``disorder`` is the unsafe/junk fraction times a small multiple of the
    rank. Intuition: a field of many safe, spread-out exemplars is high-value; a
    field cluttered with demoted (unsafe) or near-duplicate exemplars pays a tax
    -- the same answer repeated doesn't add variety, and answers the environment
    voted against subtract from confidence. ``effective_rank`` already penalizes
    redundancy (duplicates don't grow it), so this is the safe-weighted variety
    with an explicit penalty for the *wrong* answers the field has accrued.

    This is the user's "entropy-based value / free energy" riff: entropy =
    ``effective_rank`` (the variety), temperature = the gate's eagerness
    (``sim_min``/``consensus_min``), and this is the free energy -- the one number
    that says whether the field is getting richer or just bigger."""
    n = len(store)
    if n < 2:
        return 0.0
    rank = effective_rank(store, eps)
    safe = sum(1 for e in store.exemplars if e.safe)
    safe_frac = safe / n
    unsafe_frac = 1.0 - safe_frac
    disorder = 0.25 * unsafe_frac * rank       # the tax: a quarter of rank per unit disorder
    return float(safe_frac * rank - disorder)


def thermo_state(store: ExemplarStore, eps: float = 1e-9) -> dict:
    """The field's thermodynamic state from ONE SVD -- the membrane-level variety
    record. This is the variety the membrane records per decision and (opt-in)
    uses to steer its threshold (the temperature). Computing it once and handing
    the ``eff_rank`` to ``gate.decide`` reuses the SVD the gate already pays for,
    so recording variety at the membrane level adds no eigensolve -- only an O(N)
    safe-count.

    Returns ``{eff_rank, spectrum_entropy, free_energy, rank_ratio, safe_frac, n}``:

      - ``eff_rank``        = exp(Shannon entropy of the variance spectrum) -- the variety
      - ``spectrum_entropy``= log(eff_rank) -- the field's entropy in nats
      - ``free_energy``     = safe_frac*eff_rank - 0.25*unsafe_frac*eff_rank -- the VALUE number
      - ``rank_ratio``      = eff_rank / n -- the gate's novelty on/off signal
      - ``safe_frac``       = safe exemplars / n
      - ``n``               = field size

    All zero/0 for <2 exemplars (no spectrum to speak of). This is the single
    place the SVD is done for a gate turn; ``effective_rank``/``free_energy``/
    ``spectrum_entropy`` (used by the TUI/review, cached by field size) are the
    same measures exposed individually for the view layer."""
    n = len(store)
    V = store.vectors()
    if n < 2 or V.shape[1] == 0:
        return {"eff_rank": 0.0, "spectrum_entropy": 0.0, "free_energy": 0.0,
                "rank_ratio": 0.0, "safe_frac": 0.0, "n": n}
    Y = V - V.mean(axis=0)
    _, S, _ = np.linalg.svd(Y, full_matrices=False)
    var = (S ** 2) / n
    er = _entropy_rank(var, eps)
    se = float(np.log(er)) if er > 0 else 0.0
    safe = sum(1 for e in store.exemplars if e.safe)
    safe_frac = safe / n
    disorder = 0.25 * (1.0 - safe_frac) * er
    return {"eff_rank": er, "spectrum_entropy": se,
            "free_energy": float(safe_frac * er - disorder),
            "rank_ratio": er / n, "safe_frac": safe_frac, "n": n}


def principal_dirs(store: ExemplarStore
                   ) -> tuple["np.ndarray | None", "np.ndarray | None", "np.ndarray | None"]:
    """The field's center, principal directions, and variance spectrum -- the SVD
    of the centered data, exposed for two downstream uses:

      - CROSS-CELL SPECIALIZATION (the hatchery test, signature 2): which directions
        has THIS cell actually grown along? Comparing the top principal directions
        across sibling cells shows whether they DIVERSIFIED (different directions)
        or HOMOGENIZED (same field, the farm signature). The directions in FEATURE
        space are the RIGHT singular vectors (Vt), not U (the _principal_dirs
        footgun guard).
      - SHARED SVD for novelty/curiosity: ``novelty`` currently does its own SVD
        (variety.py:240), so a curiosity-everywhere mode pays a second SVD per turn.
        Refactoring ``novelty`` to accept this precomputed (mu, Vt, var) is the
        one-line move that removes that doubling -- noted, not required for the
        small-D experiment.

    Returns ``(mu, Vt, var)`` or ``(None, None, None)`` for a field with <2 exemplars
    (no spectrum). Does NOT change ``thermo_state`` (which keeps only ``S``) -- this
    is additive, so the existing per-decision path is untouched.
    """
    V = store.vectors()
    if len(store) < 2 or V.shape[1] == 0:
        return None, None, None
    mu = V.mean(axis=0)
    Y = V - mu
    var, Vt = _principal_dirs(Y)
    return mu, Vt, var


def orient_dirs(store: ExemplarStore, max_k: int, cos_sep: float = 0.5
                 ) -> list[np.ndarray]:
    """The parent's DISTINCT experienced directions -- unit vectors drawn one per
    mutually-``cos_sep``-apart region of the field, for budding (stage 4).

    Min-separation greedy: walk the field's unit vectors and keep a direction only if
    it is more than ``cos_sep`` (cosine) from every direction already kept. For an
    orthogonal-skill geometry this yields ~one direction per skill (orthogonal bases
    are cos 0 apart, well under ``cos_sep``; same-base paraphrases are cos ~0.95, kept
    once). The result is a small set of clean per-skill orientations -- the directions
    the parent ACTUALLY grew along -- with no central router and no cluster-count knob.

    WHY NOT ``principal_dirs`` (the SVD right singular vectors): a GENERALIST parent
    grows roughly evenly across its skills, so its centered field has ~isotropic
    variance and the SVD's principal directions are DEGENERATE (near-equal singular
    values -> any rotation within the skill subspace is a valid solution -> Vt comes
    back as arbitrary mixtures of the skill axes, not the axes themselves). Orienting a
    child at such a mixture points it at no single skill. ``orient_dirs`` sidesteps the
    degeneracy: it picks REPRESENTATIVE exemplars (each ~one skill axis plus veil
    noise), which are well-defined for any parent, even or uneven. This is the
    decentralized-inheritance point held: the orientation comes from the parent's OWN
    field geometry, not from a central router -- just from its distinct directions
    rather than its (ill-conditioned, for a generalist) variance axes.

    Deterministic order: candidates are tried most-aligned-with-the-field-mean first
    (a representative of a real cluster, not an outlier), so the seed is central. If
    the parent has fewer than ``max_k`` separable directions, returns all of them (the
    caller -- ``Cell.bud`` -- treats an out-of-range ``direction_index`` as "the parent
    has not grown in that direction yet" -> the child boots unoriented, the honest
    "not ready to bud" state whose TRIGGER is stage 5)."""
    V = store.vectors()
    n = len(store)
    if n == 0 or V.shape[1] == 0:
        return []
    U = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    mu = U.mean(axis=0)
    mu = mu / (np.linalg.norm(mu) + 1e-12)
    order = sorted(range(n), key=lambda i: -(U[i] @ mu))
    picked: list[int] = []
    for i in order:
        if all((U[i] @ U[j]) < cos_sep for j in picked):
            picked.append(i)
        if len(picked) >= max_k:
            break
    return [U[i] for i in picked]


def aligned_safe_slice(store: ExemplarStore, orientation: np.ndarray,
                       align_min: float = 0.5, max_n: int = 8) -> list:
    """The parent's SAFE exemplars aligned with a budding orientation -- the SEED a
    child inherits (stage 5, the energy economy).

    Filters ``store.exemplars`` to those that are (1) ``safe`` (proven by the
    two-authority boundary -- the environment did not vote them down) and (2)
    aligned with ``orientation`` beyond ``align_min`` (cosine), then takes the
    ``max_n`` MOST aligned. The seed is the child's starting competence in its
    inherited skill: proven-safe forwards the parent produced WHILE SERVING, not a
    harvest of the shared buffer. This is sig 3 (byproduct-not-extraction): the
    child inherits the parent's BYPRODUCT (the safe variety it grew by forwarding
    real turns), not an EXTRACTION (buffer content taken without serving).

    The exemplars are returned as-is (the parent's veiled ``vec`` + its safety
    evidence ``safe``/``reask_count``/``contest_count`` kept) -- the child inherits a
    PROVEN-safe perspective and does not re-litigate the parent's safety. Its own
    forwards add child-veiled exemplars, so the grown field is inheritance + growth.
    ``max_n`` caps the seed so the child still has room to grow its own variety
    (a full-saturation seed would leave it nothing to be curious about).

    ``orientation`` is (re)normalized to unit length so ``align`` is a true cosine.
    Returns ``[]`` for an empty store or an orientation nothing aligns with."""
    n = len(store)
    if n == 0 or orientation is None:
        return []
    V = store.vectors()
    on = np.asarray(orientation, dtype=float)
    on = on / (np.linalg.norm(on) + 1e-12)
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    sims = Vn @ on
    # most-aligned first; keep only safe + aligned; cap at max_n
    order = sorted(range(n), key=lambda i: -float(sims[i]))
    return [store.exemplars[i] for i in order
            if store.exemplars[i].safe and float(sims[i]) >= align_min][:max_n]


def spread(store: ExemplarStore) -> float:
    """Mean pairwise cosine distance -- a crude, cheap variety proxy.

    ~0 means every exemplar is (nearly) identical; larger means more spread out.
    Cheaper than effective_rank (no eigensolve) and a good sanity cross-check.
    """
    V = store.vectors()
    if len(store) < 2:
        return 0.0
    Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
    G = Vn @ Vn.T
    n = len(store)
    iu = np.triu_indices(n, k=1)        # each unordered pair once
    return float(np.mean(1.0 - G[iu]))


def _principal_dirs(Y: np.ndarray):
    """SVD of centered data Y (n, D) -> (variances, right-singular directions Vt).

    The principal directions in FEATURE space are the RIGHT singular vectors (Vt),
    not U. U spans the row space (n-dim); using it is the classic SVD-on-samples
    footgun (gives a dimension mismatch against a D-dim query).
    """
    U, S, Vt = np.linalg.svd(Y, full_matrices=False)
    var = (S ** 2) / max(Y.shape[0], 1)   # variance along each principal direction
    return var, Vt


def novelty(
    query: np.ndarray,
    store: ExemplarStore,
    eps: float = 1e-9,
    keep_fraction: float | None = None,
    keep: int | None = None,
) -> float:
    """How far the query sits OFF the store's experienced manifold.

    Returns the query's off-subspace residual divided by sqrt(gamma), where gamma
    is the mean variance in the DELIBERATELY DROPPED directions (the null-space
    width). High -> the query is in a direction the field has little variety in
    -> the gate should escalate, *even if its cosine to a neighbor is high*.

    This is the rosetta-check Mahalanobis idea, done simply and correctly. The
    under-keeping (KEEP_FRACTION < 1) is load-bearing: with full effective rank
    kept, gamma -> 0 and novelty blows up; the deliberately dropped directions are
    what give the null space a real scale to measure against.

    Consolidation knob (the "massage" parameter): by default the field keeps
    KEEP_FRACTION of its effective rank. ``keep_fraction`` scales that fraction;
    ``keep`` pins an exact number of kept directions. Keeping FEWER directions =
    compressing the field harder into its essential shape = a larger null space.
    That is exactly what the consolidate experiment sweeps: does some compression
    level produce a usable absorb/escalate valley where the default (0.8) blob
    had none? The default call is unchanged (uses KEEP_FRACTION).
    """
    V = store.vectors()
    n = len(store)
    if n < 2:
        return 1.0                       # nothing experienced -> maximally novel
    mu = V.mean(axis=0)
    Y = V - mu
    var, Vt = _principal_dirs(Y)

    # how many directions count as "experienced" -- always drop at least one so
    # the null space has a real gamma.
    if keep is not None:
        r = int(keep)
    else:
        frac = KEEP_FRACTION if keep_fraction is None else keep_fraction
        r = int(round(frac * _entropy_rank(var)))
    r = max(1, min(len(var) - 1, r))

    dropped_var = var[r:]
    gamma = float(np.mean(dropped_var)) if dropped_var.size else 0.0
    gamma = max(gamma, eps)

    # off-subspace residual of the query
    q = np.asarray(query, dtype=float) - mu
    basis = Vt[:r].T                    # (D, r)
    recon = basis @ (basis.T @ q)
    resid = float(np.linalg.norm(q - recon))
    return float(resid / np.sqrt(gamma))