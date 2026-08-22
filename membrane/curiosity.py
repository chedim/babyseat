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
               eps: float = 1e-9, keep_fraction: float | None = None) -> bool:
    """Should this cell PULL this situation into its field?

    True when the situation is off the cell's experienced manifold
    (``curiosity_score > curiosity_min``) and not a near-duplicate of something
    already absorbed (a dup adds no variety). Field-relative (depends on THIS cell's
    store), self-extinguishing (growing toward the direction lowers the score next
    time). The default ``curiosity_min`` is the same line as the gate's novelty veto
    (opposite polarity); ``dup_sim`` defaults to the learn-time dedup threshold.

    A field with <2 exemplars is always curious (the young-cell bootstrap: take
    everything to build the first variety, then curiosity starts discriminating)."""
    return assess(query_vec, store, curiosity_min, dup_sim, eps, keep_fraction)[0]


def assess(query_vec: np.ndarray, store: ExemplarStore,
           curiosity_min: float = CURIOSITY_MIN, dup_sim: float = 0.95,
           eps: float = 1e-9, keep_fraction: float | None = None
           ) -> tuple[bool, float]:
    """``is_curious`` AND its score in ONE novelty computation (one SVD).

    Returns ``(curious, score)``. The greedy pull loop (membrane/cell.py) evaluates
    many candidates per step; calling ``is_curious`` then ``curiosity_score`` would
    compute the null-space residual twice. This does it once and applies the policy
    (young-cell bootstrap / dup safety / threshold) to the same score. ``score`` is
    the curiosity signal regardless of the bool (useful for "take the MOST curious
    first" -- grow toward the most unabsorbed direction)."""
    if len(store) < 2:
        # young cell: bootstrap. Score is novelty's "nothing experienced" sentinel;
        # everything is curious. (Score 1.0 so greedy still orders the first takes.)
        return True, 1.0
    if dup_sim <= 1.0 and _near_dup(query_vec, store, dup_sim):
        return False, 0.0                   # safety: a dup adds no variety
    score = curiosity_score(query_vec, store, eps, keep_fraction)
    return score > curiosity_min, score


def _near_dup(query_vec: np.ndarray, store: ExemplarStore, dup_sim: float) -> bool:
    """True if a near-identical situation is already in the field (cosine >=
    ``dup_sim``) -- the same guard as ``Membrane._find_dup``, consulted here so a
    cell does not pull something it has already absorbed."""
    if len(store) == 0:
        return False
    qn = query_vec / (np.linalg.norm(query_vec) + 1e-12)
    near, sim = store.top_k(qn, k=1)[0]
    return sim >= dup_sim