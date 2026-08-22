"""Curiosity-driven intake: the PULL decision (which situations a cell takes into
its field), as the opposite-telos twin of the confidence gate.

The gate (membrane/gate.py) is the CONSERVATIVE move: it protects the cell from
ACTING on what it hasn't absorbed -- high novelty (off-manifold) is a VETO ("too
novel to replay -> escalate"). Curiosity inverts the polarity on the SAME signal:
high novelty (off-manifold, unabsorbed) is an ATTRACTOR -- the cell is drawn to TAKE
it, because taking it grows the field's variety in a direction the cell lacks. Same
null-space residual, opposite telos: control theory says "collapse the disturbance
to a controlled output"; the membrane says "grow the variety to absorb more of the
world." This is Ashby's law with the control-theory skin peeled off.

Curiosity is the PULL decision (do I take this situation into my field?), NOT the
replay decision (the gate's act-vs-escalate). The two are cleanly separated: the
gate decides replay-vs-escalate on the situations a cell has ALREADY taken; curiosity
decides which situations to take in the first place. Replacing the gate's veto with
an attractor would conflate them and break the conservative mode; instead curiosity
is a PARALLEL, opt-in function, and the gate (gate.decide, gate.py:85-86) is
untouched. When curiosity is off (the default) nothing changes -- the existing path
is bit-identical.

Three properties, by construction:

  - FIELD-RELATIVE: the same situation is interesting to one cell and boring to
    another, because each cell's experienced manifold is different. Novelty is
    measured against THIS cell's store, so two cells pulling the same shared buffer
    take different subsets -- which is specialization (the hatchery's "diversifies,
    not homogenizes" signature), emerging from each cell's own curiosity, with no
    central router telling cells what to take.

  - SELF-EXTINGUISHING: once a cell grows toward a direction, situations in that
    direction stop being off-manifold -> novelty drops -> the cell stops pulling
    them. Curiosity is self-limiting: it seeks the unabsorbed, and absorbing
    extinguishes the seeking. A cell cannot chase one direction forever; it grows
    until that direction is absorbed, then moves on. This is what keeps curiosity
    from being a runaway.

  - PAIRS WITH SAFETY (curiosity seeks, safety keeps): pure curiosity would chase
    the infinitely-novel -- random noise reads as maximally off-manifold. Two
    guards prevent the noise trap. (1) The BOUNDARY: the shared buffer is fed only
    by real user turns -- a cell cannot feed itself (it has no deposit path) -- so
    the situations are human-authored, not random garbage. The safety is partly
    structural, in the feeding boundary, not in the curiosity function. (2) The DUP
    guard: a near-duplicate of something already absorbed adds no variety -> not
    curious. (Novelty is already low for a near-dup; this is belt-and-suspenders
    against the high-cosine/off-manifold acquisition gap.) The learn-time guards
    (vague filter, dedup) in ``Membrane._learn`` are the further safety net.

The threshold default is the SAME line as the gate's novelty veto
(``gate.novelty_max = 2.0``), opposite polarity: the gate says "nov > this -> too
novel to REPLAY"; curiosity says "nov > this -> novel enough to PULL". One line,
two telos.
"""

from __future__ import annotations

import numpy as np

from .field import ExemplarStore
from .variety import novelty

# The curiosity threshold defaults to the SAME line as the gate's novelty veto
# (gate.decide's novelty_max = 2.0), opposite polarity. The gate: "nov > 2.0 -> too
# novel to replay (escalate)". Curiosity: "nov > 2.0 -> novel enough to pull (take)".
# One signal, opposite telos -- the conservative-protects / curious-seeks pair.
CURIOSITY_MIN = 2.0


def curiosity_score(query_vec: np.ndarray, store: ExemplarStore,
                   eps: float = 1e-9, keep_fraction: float | None = None) -> float:
    """How off-manifold (unabsorbed) ``query_vec`` is relative to ``store`` -- the
    curiosity signal. Returns the null-space residual (``variety.novelty``). High ->
    the situation is in a direction the field has little variety in -> taking it
    would grow variety there -> the cell is drawn to it. 1.0 for a field with <2
    exemplars (nothing experienced yet -> everything is novel -> a young cell is
    hungry, which is the correct "variance first" bootstrap)."""
    return float(novelty(query_vec, store, eps=eps, keep_fraction=keep_fraction))


def is_curious(query_vec: np.ndarray, store: ExemplarStore,
               curiosity_min: float = CURIOSITY_MIN, dup_sim: float = 0.95,
               eps: float = 1e-9, keep_fraction: float | None = None,
               orientation: np.ndarray | None = None,
               orientation_strength: float = 0.0) -> bool:
    """Should this cell PULL this situation into its field?

    True when the situation is off the cell's experienced manifold
    (``curiosity_score > curiosity_min``) and not a near-duplicate of something
    already absorbed (a dup adds no variety). Field-relative (depends on THIS cell's
    store), self-extinguishing (growing toward the direction lowers the score next
    time). The default ``curiosity_min`` is the same line as the gate's novelty veto
    (opposite polarity); ``dup_sim`` defaults to the learn-time dedup threshold.

    A field with <2 exemplars is always curious (the young-cell bootstrap: take
    everything to build the first variety, then curiosity starts discriminating).

    ``orientation`` (stage 4, budding): an optional unit direction in embed space the
    cell is biased toward -- inherited from a parent cell's distinct experienced
    direction (membrane/variety.py ``orient_dirs``). A situation aligned with it is
    curious (passes the hard ``_aligned`` gate); one off the orientation is BLOCKED
    (the gate, load-bearing for specialists) and down-ranked (``_orient_factor``).
    ``orientation_strength=0.0`` or ``orientation=None`` -> no bias (identity)."""
    return assess(query_vec, store, curiosity_min, dup_sim, eps, keep_fraction,
                  orientation, orientation_strength)[0]


def assess(query_vec: np.ndarray, store: ExemplarStore,
           curiosity_min: float = CURIOSITY_MIN, dup_sim: float = 0.95,
           eps: float = 1e-9, keep_fraction: float | None = None,
           orientation: np.ndarray | None = None,
           orientation_strength: float = 0.0
           ) -> tuple[bool, float]:
    """``is_curious`` AND its score in ONE novelty computation (one SVD).

    Returns ``(curious, score)``. The greedy pull loop (membrane/cell.py) evaluates
    many candidates per step; calling ``is_curious`` then ``curiosity_score`` would
    compute the null-space residual twice. This does it once and applies the policy
    (young-cell bootstrap / dup safety / threshold) to the same score. ``score`` is
    the curiosity signal regardless of the bool (useful for "take the MOST curious
    first" -- grow toward the most unabsorbed direction).

    ``orientation`` (stage 4, budding): a unit direction the cell is biased toward
    (inherited from a parent's distinct experienced direction -- membrane/variety.py
    ``orient_dirs``). Orientation acts on curiosity in TWO ways, both default-off
    (``orientation=None`` / ``orientation_strength=0.0`` -> bit-identical to stage 3):

      - HARD GATE (``_aligned``, load-bearing): ``curious`` ALSO requires the situation
        be aligned with the orientation beyond ``strength/(1+strength)``. This is what
        makes a budded cell a SPECIALIST: it blocks off-base situations independent of
        the novelty scale (a specialist's near rank-1 field makes ``novelty`` blow up
        for every off-base query, so the soft factor alone can't suppress it).
      - SOFT FACTOR (``_orient_factor``, ranking): ``score`` is multiplied by the
        factor so greedy "most curious first" orders the takes toward the orientation.

    The young-cell bootstrap returns ``aligned`` (not a blanket True), so an oriented
    young cell takes ONLY aligned situations from the start (still ordered by the
    factor). Self-extinguishing survives: the owned base's paraphrases are near-dups
    (the dup guard skips them) and, once the field has rank, the owned direction's
    novelty drops; the off-base directions are blocked by the gate regardless."""
    factor = _orient_factor(query_vec, orientation, orientation_strength, eps)
    aligned = _aligned(query_vec, orientation, orientation_strength, eps)
    if len(store) < 2:
        # young cell: bootstrap. Score is novelty's "nothing experienced" sentinel;
        # everything is curious -- UNLESS an orientation is set, in which case the young
        # cell is curious only about situations ALIGNED with its inherited direction
        # (``aligned``). Score ``1.0 * factor`` so greedy still orders the first takes,
        # biased toward the orientation. (Unoriented -> aligned True -> bit-identical.)
        return aligned, 1.0 * factor
    if dup_sim <= 1.0 and _near_dup(query_vec, store, dup_sim):
        return False, 0.0                   # safety: a dup adds no variety
    score = curiosity_score(query_vec, store, eps, keep_fraction) * factor
    # The hard alignment gate (``and aligned``) is LOAD-BEARING for specialists: a
    # specialist's field is near rank-1, so ``novelty`` blows up (gamma -> 0 -> nov ~
    # thousands) for EVERY off-base situation, and the multiplicative ``factor`` (<1)
    # cannot suppress a signal on that scale back under ``curiosity_min``. The gate
    # blocks non-aligned situations INDEPENDENT of novelty scale -> a specialist stops
    # grazing other skills. Unoriented -> ``aligned`` always True -> ``score > min``
    # alone -> bit-identical to stage 3.
    return (score > curiosity_min) and aligned, score


def _near_dup(query_vec: np.ndarray, store: ExemplarStore, dup_sim: float) -> bool:
    """True if a near-identical situation is already in the field (cosine >=
    ``dup_sim``) -- the same guard as ``Membrane._find_dup``, consulted here so a
    cell does not pull something it has already absorbed."""
    if len(store) == 0:
        return False
    qn = query_vec / (np.linalg.norm(query_vec) + 1e-12)
    near, sim = store.top_k(qn, k=1)[0]
    return sim >= dup_sim


def _orient_factor(query_vec: np.ndarray, orientation: np.ndarray | None,
                   strength: float, eps: float = 1e-9) -> float:
    """The budding bias (stage 4): how much MORE or LESS curious a situation is because
    of the cell's inherited ``orientation``. Returns a multiplier on the raw curiosity
    score:

      ``factor = (1 + strength * align) / (1 + strength)``,  ``align = cosine(query, orientation)``

    - ``align = +1`` (the situation IS the oriented skill) -> ``factor = 1.0`` (full
      curiosity, unchanged -- the cell still owns what it's oriented toward).
    - ``align =  0`` (orthogonal / a DIFFERENT skill) -> ``factor = 1/(1+strength)`` < 1
      (SUPPRESSED -- this is what makes a budded cell a specialist, not a generalist:
      once its own skill saturates, the orthogonal bases' ``score = novelty * factor``
      sits under ``curiosity_min`` so it stops, instead of grazing every skill).
    - ``align = -1`` (anti-aligned) -> ``factor = (1-strength)/(1+strength)``.
    - ``strength = 0`` or ``orientation = None`` -> ``factor = 1.0`` (IDENTITY: the
      unoriented path is bit-identical to stage 3 -- budding is opt-in, default off).

    The NORMALIZED multiplicative form (not additive) is load-bearing for orthogonal
    skill geometries: an additive ``novelty + s*align`` bias adds nothing at ``align=0``
    (orthogonal bases), so the cell would still graze other skills once its own saturates
    -> no single-skill ownership. The normalized form makes ``align=0`` suppress, which
    is what produces a specialist. Both ``query_vec`` and ``orientation`` are
    (re)normalized to unit length so ``align`` is a true cosine in [-1, 1]."""
    if orientation is None or strength == 0.0:
        return 1.0
    qn = query_vec / (np.linalg.norm(query_vec) + eps)
    on = orientation / (np.linalg.norm(orientation) + eps)
    align = float(qn @ on)
    return (1.0 + strength * align) / (1.0 + strength)


def _aligned(query_vec: np.ndarray, orientation: np.ndarray | None,
             strength: float, eps: float = 1e-9) -> bool:
    """The hard selection gate (stage 4): is ``query_vec`` aligned enough with the cell's
    inherited ``orientation`` to be CURIOUS about it? True (curious allowed) when no
    orientation is set or ``strength == 0`` (IDENTITY -- the unoriented path is
    bit-identical to stage 3); otherwise ``cosine(query, orientation) > strength/(1+strength)``.

    This is the LOAD-BEARING half of orientation (the multiplicative ``_orient_factor``
    is the soft ranking half). A specialist's field is near rank-1, so ``novelty`` is
    ill-conditioned (gamma -> 0 -> novelty ~ thousands for ANY off-base situation); the
    soft factor (a multiplier in ~(0, 1]) cannot suppress a signal on that scale back
    under ``curiosity_min``. The hard gate blocks off-base situations INDEPENDENT of the
    novelty scale, so a budded specialist takes its own base and stops, instead of
    grazing every skill. The threshold is derived from ``strength`` -- higher strength
    -> stricter selection (``strength/(1+strength)``: 1->0.5, 2->0.67, 4->0.8) -- so the
    one knob ``orientation_strength`` controls how hard the orientation selects, with 0
    the identity (no gate). Both query and orientation are (re)normalized to unit length
    so ``align`` is a true cosine in [-1, 1]."""
    if orientation is None or strength <= 0.0:
        return True
    qn = query_vec / (np.linalg.norm(query_vec) + eps)
    on = orientation / (np.linalg.norm(orientation) + eps)
    align = float(qn @ on)
    return align > strength / (1.0 + strength)