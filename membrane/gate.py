"""The confidence gate: COSINE-PRIMARY, with null-space novelty as a conditional
second signal.

Why cosine-primary: the real-embedding test (experiments/real_knee.py) showed that
on dense mxbai embeddings a modest field is HIGH-rank (effective_rank ~ N), so the
null-space residual `novelty` is near-constant for every query and useless as a
gate -- it collapses the routine/novel separation that plain nearest-cosine keeps
(93% false-escalation vs cosine's 0%). The null space is only an *asset* when it
is LARGE, i.e. a LOW-rank field (a narrow, saturated domain). So:

  - cosine (nearest-similarity) is the PRIMARY filter -- it works on real, dense,
    high-rank fields.
  - novelty is a CONDITIONAL veto, applied only when the field is low-rank
    (`effective_rank / len(field)` below `novelty_rank_ratio_max`). There it can
    catch the acquisition gap: a query that is high-cosine (sim says "act") yet
    off-manifold (novelty says "escalate"). On high-rank fields it is OFF -- it
    would only add noise.

This re-derives rosetta-check's own tiering: cosine is the default; the
covariance/Mahalanobis operator is opt-in for narrow trained (low-rank) fields.

not-sure = low nearest-sim OR low neighbor-consensus, OR (only when the field is
low-rank) high novelty. Confidence and safety are separate axes (the reversibility
guard): a confident match on a risky action still escalates -- with the recalled
action as a hint, never pulling the trigger itself.
"""

from __future__ import annotations

import numpy as np

from .field import ExemplarStore
from .variety import novelty, effective_rank


def decide(
    query: np.ndarray,
    store: ExemplarStore,
    k: int = 5,
    sim_min: float = 0.70,
    consensus_min: float = 0.60,
    novelty_max: float = 2.0,
    novelty_rank_ratio_max: float = 0.5,
) -> dict:
    """Route `query` against `store` to one of: act / escalate-with-hint / escalate.

    Thresholds are calibration knobs; `sim_min` is the cosine cutoff that the
    real-embedding knee experiment finds from the workload (not guessed). The
    `novelty_*` knobs only matter on low-rank fields; on high-rank fields novelty
    is not consulted and they are inert.

    Returns:
      {"verdict": "act",                "action": ..., "sim": ..., "nov": ...|None, "ex_idx": int|None}
      {"verdict": "escalate-with-hint", "hint":   ..., "sim": ..., "nov": ...|None, "ex_idx": int|None}
      {"verdict": "escalate",           "reason": ..., "sim": ..., "nov": ...|None, "ex_idx": None}
    """
    if len(store) == 0:
        return {"verdict": "escalate", "reason": "empty store", "sim": 0.0, "nov": None,
                "ex_idx": None}

    qn = query / (np.linalg.norm(query) + 1e-12)
    nbrs = store.top_k(qn, k=k)
    near, sim = nbrs[0]
    near_idx = store.index_of(near)              # identity lookup; the training loop uses this
    actions = [a.action for a, _ in nbrs]
    consensus = sum(1 for a in actions if a == near.action) / len(actions)

    # novelty is conditional: only trustworthy when the field is low-rank (a real
    # null space exists). On a high-rank dense field the residual is near-constant
    # and novelty only adds noise, so it is not consulted.
    rank_ratio = effective_rank(store) / max(len(store), 1)
    use_novelty = rank_ratio < novelty_rank_ratio_max and len(store) >= 3
    nov = novelty(query, store) if use_novelty else None

    sure = sim >= sim_min and consensus >= consensus_min
    if use_novelty:
        sure = sure and nov <= novelty_max

    signals = f"sim={sim:.2f} cons={consensus:.2f}" + (f" nov={nov:.2f}" if use_novelty else " nov=off(rank)")

    if not sure:
        return {"verdict": "escalate", "reason": f"not sure ({signals})", "sim": sim, "nov": nov,
                "ex_idx": None}
    if near.safe:
        return {"verdict": "act", "action": near.action, "sim": sim, "nov": nov, "ex_idx": near_idx}
    return {"verdict": "escalate-with-hint", "hint": near.action, "sim": sim, "nov": nov,
            "ex_idx": near_idx}