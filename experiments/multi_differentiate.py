#!/usr/bin/env python3
"""Multi-differentiate experiment -- the REVERSE-OF-BUDDING DEATH TEST (stage 6).

Budding (stage 4, experiments/multi_bud.py) is the FORWARD operation: a generalist
parent buds a specialist CHILD oriented toward one of its distinct experienced
directions. The parent stays WHOLE; the child's specialization is a soft,
default-off curiosity bias -- not a cut.

DIFFERENTIATION (stage 6) is the REVERSE: a cell cuts ITS OWN field down to one
experienced direction (the same selector budding uses to seed a child --
``variety.aligned_safe_slice`` -- but keeping only the aligned slice and dropping
the rest) and LOCKS that direction as a hard alignment gate. This is the move
budding avoids. It is the `the endless environment.md` §4 "reverse of budding":
the part gives up its variety (paves its null space, effective rank -> 1, toward
crystal death) to become a specialist part.

This experiment reads the §4 prediction -- a STANDALONE differentiated specialist
is DEAD on its orthogonal complement -- against the cell it was before:

  SIG 6a -- CUTS STRUCTURE: differentiate collapses the field's ``effective_rank``
            and its ``orient_dirs`` count (the variety it used to carry) toward 1,
            and ``free_energy`` with it (the crystal-death minimum, §2).
  SIG 6b -- DEAD ON THE ORTHOGONAL COMPLEMENT: a generalist ACTS (gate `decide` =
            ``act``) on every base it covers; after differentiating toward one
            base, it STILL acts on that base but ESCALATES on the rest -- the
            bases it used to cover are now beyond it. The gate loss (act ->
            escalate) on the orthogonal complement is the death signature. (Phase
            2, experiments not yet built, tests the resurrection: a ROUTER over
            differentiated specialists covers what no single one can.)

The death here is a test of the §4 READING, not a proof of it. The truncation step
is the §4.7 "SVD truncation = coarse-graining" claim, which is still unvetted;
this experiment uses SELECTION (drop the orthogonal exemplars), which reuses
``aligned_safe_slice`` cleanly and matches "pave the null space," and is NOT the
literal low-rank projection of §4.7 (a deferred ``mode=`` variant). The §4.7
claim stays open regardless of the lab outcome.

Runs with no Ollama / no Anthropic / no real key (fake embedder + base-keyed
responder, reused from multi_cell.py). All field/audit under a temp dir; nothing
leaks to the repo.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.buffer import BufferView, SituationBuffer
from membrane.cell import Cell
from membrane.curiosity import _aligned
from membrane.gate import decide
from membrane.variety import effective_rank, orient_dirs, thermo_state

# Reuse the layer-1 harness: the fake geometry, the workload, the cell builder,
# and the formatter. Differentiation adds only the cut + the before/after probe.
from experiments.multi_cell import (  # noqa: E402
    BASES, BASE_KEYS, WORKLOAD, FakeEmbedder, BaseKeyedResponder, _build_cell,
)
from experiments.multi_veil import _f  # noqa: E402

# One representative probe per base -- a workload text the generalist will have
# PULLED (so its exemplar is in the field). FakeEmbedder maps each to its base's
# direction; the gate's nearest-neighbour verdict tests recall, not novelty.
PROBES = {
    "parse":    "how do I parse the json file",
    "deploy":   "deploy the service to prod",
    "test":     "run the test suite",
    "debug":    "debug the crash on startup",
    "refactor": "refactor the handler module",
    "docs":     "write the readme docs",
}


def _gate_verdict(cell: Cell, text: str) -> tuple[str, float]:
    """The gate's act/escalate verdict on ``text`` against the cell's CURRENT field,
    mirroring ``gate_turn`` (embed -> veil -> decide) without the re-ask path."""
    vec = cell._situation_vec(text)                 # embed + THIS cell's veil
    thermo = thermo_state(cell.membrane.store)
    v = decide(vec, cell.membrane.store,
               k=getattr(cell.membrane, "k", 5),
               sim_min=cell.membrane.sim_min,
               consensus_min=cell.membrane.consensus_min,
               eff_rank=thermo["eff_rank"],
               novelty_sim_max=cell.membrane.dedup_sim)
    return v["verdict"], float(v.get("sim") or 0.0)


def _aligned_verdict(cell: Cell, text: str) -> bool:
    """The hard alignment gate (curiosity._aligned) on ``text`` against the cell's
    committed orientation: True = pull allowed (on the owned subspace), False =
    blocked (off it). Re-veils under the cell's own veil for a fair cosine."""
    vec = cell._situation_vec(text)
    return _aligned(vec, cell.orientation, cell.orientation_strength)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-diff-")
    print("multi-differentiate experiment -- the reverse-of-budding death test")
    print("(stage 6: a cell cuts its OWN field to one direction -- the anti-bud)\n")

    # ---- 1. grow a GENERALIST (curious, veil on) over the shared buffer --------
    buf = SituationBuffer()
    for text in WORKLOAD:
        buf.deposit(text)
    view = BufferView(buf)
    cell = _build_cell("generalist",
                      {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}, tmp)
    # TOY CALIBRATION (honest): _build_cell sets sim_min=0.85, the LIVE proxy's
    # line. In this toy's D=8 / nudge_sigma=0.25 regime two independently-veiled
    # SAME-base vecs sit at cosine ~0.69 (the veil noise is a large fraction of
    # the unit signal at D=8), so 0.85 is too strict for a specialist to ACT on
    # its OWN base (its nearest kept neighbor lands ~0.80). 0.70 is the gate's
    # OWN default (gate.py:36) and sits below the same-base band (~0.69-0.92)
    # and above the cross-base band (~0.1-0.25) -- so a specialist acts on its
    # owned base and escalates on the rest. The real 1024-d mxbai regime (same-
    # semantic cosine ~0.9+ natively, veil a small perturbation) keeps 0.85.
    cell.membrane.sim_min = 0.70
    devnull = open(os.devnull, "w")
    with contextlib.redirect_stderr(devnull):
        cell.pull(view)

    n_before = len(cell.membrane.store)
    rank_before = effective_rank(cell.membrane.store)
    dirs_before = orient_dirs(cell.membrane.store, len(BASES), 0.5)
    fe_before = float(thermo_state(cell.membrane.store)["free_energy"])
    print(f"generalist pulled {n_before} situations across the {len(WORKLOAD)}-text "
          f"workload (~{len(BASES)} bases)")
    print(f"  effective_rank        = {_f(rank_before)}  (the veil spreads it past "
          f"the ~{len(BASES)} intrinsic bases)")
    print(f"  distinct directions   = {len(dirs_before)}  (orient_dirs)")
    print(f"  free_energy           = {_f(fe_before)}  (alive: high/off-minimum)")

    # ---- 2. gate verdict per base BEFORE (a generalist acts on every base) -----
    print("\ngate verdict per base BEFORE differentiate "
          "(a generalist ACTS on every base it covers):")
    verdicts_before: dict[str, str] = {}
    for k in BASE_KEYS:
        v, sim = _gate_verdict(cell, PROBES[k])
        verdicts_before[k] = v
        print(f"  {k:<10} -> {v:<16} (sim={_f(sim)})")
    acted_before = [k for k in BASE_KEYS if verdicts_before[k] == "act"]

    # ---- 3. DIFFERENTIATE toward direction_index=0 ----------------------------
    # gate_strength=1.0 -> hard alignment gate at cosine > 0.5: in this toy's
    # D=8/0.25-veil band the OWNED base sits at cosine ~0.69 to the orientation
    # and the OFF bases at ~0.1, so 0.5 separates them with margin. (multi_bud
    # uses 2.0/0.67 for its pull-time ownership test; here a FRESH probe vs the
    # orientation is a harder match, so 1.0/0.5 gives the cleaner margin. The
    # real 1024-d regime, where owned ~0.97 and off ~0.05, uses 4.0/0.80.)
    orientation = cell.differentiate(direction_index=0, gate_strength=1.0)
    if orientation is None:
        print("\nNOT READY: the generalist has too few separable directions to "
              "differentiate (orient_dirs < 1). Grow it longer. RED.")
        print(f"\nartifacts: {tmp}")
        return

    # which base did the cell commit to? (the orientation's alignment with each base)
    al = {k: float(np.asarray(BASES[k]) @ orientation) for k in BASE_KEYS}
    owned = max(al, key=al.get)
    n_after = len(cell.membrane.store)
    rank_after = effective_rank(cell.membrane.store)
    dirs_after = orient_dirs(cell.membrane.store, len(BASES), 0.5)
    fe_after = float(thermo_state(cell.membrane.store)["free_energy"])
    threshold = cell.orientation_strength / (1.0 + cell.orientation_strength)
    print(f"\nDIFFERENTIATED toward direction_index=0 -> owns '{owned}' "
          f"(alignment {_f(al[owned])} vs next {_f(max(v for kk,v in al.items() if kk!=owned))})")
    print(f"  field cut             {n_before} -> {n_after} exemplars "
          f"(dropped the orthogonal complement)")
    print(f"  effective_rank        {_f(rank_before)} -> {_f(rank_after)}  "
          f"(collapsed toward 1 -> crystal death)")
    print(f"  distinct directions   {len(dirs_before)} -> {len(dirs_after)}")
    print(f"  free_energy           {_f(fe_before)} -> {_f(fe_after)}  "
          f"(toward the minimum -- the death, §2)")
    print(f"  orientation_strength  -> {cell.orientation_strength}  "
          f"(hard gate at cosine > {_f(threshold)})")

    # ---- 4. gate verdict per base AFTER (acts on owned, escalates the rest) ---
    print("\ngate verdict per base AFTER differentiate "
          "(acts on the owned base, ESCALATES on the rest):")
    verdicts_after: dict[str, str] = {}
    dead: list[str] = []
    for k in BASE_KEYS:
        v, sim = _gate_verdict(cell, PROBES[k])
        verdicts_after[k] = v
        was = verdicts_before[k]
        marker = ""
        if was == "act" and v != "act":
            dead.append(k)
            marker = "   <- DEAD here (act -> escalate)"
        own_marker = "   [owned]" if k == owned else ""
        print(f"  {k:<10} -> {v:<16} (sim={_f(sim)})  [was {was}]{marker}{own_marker}")

    print(f"\nalignment gate (_aligned, threshold {_f(threshold)}) per base AFTER "
          f"(the refusal that stops the lost directions re-growing):")
    for k in BASE_KEYS:
        a = _aligned_verdict(cell, PROBES[k])
        print(f"  {k:<10} -> {'ALIGNED  (pull allowed)' if a else 'BLOCKED  (pull refused)'}"
              f"{'   [owned]' if k == owned else ''}")

    # ---- 5. verdict -----------------------------------------------------------
    print("\n=== reverse-of-budding death test -- verdict ===")
    # sig 6a: the structure is cut. The CLEAN collapse is directional (orient_dirs
    # 6 -> 1: the field now spans one direction). effective_rank drops too but
    # only PARTLY -- the kept aligned slice is a veil-spread CLOUD, not a line, so
    # its rank stays ~2-4 (honest: selection paves the null space, it does not
    # pin rank to 1; that would need projection). So 6a keys on directions.
    dirs_collapsed = len(dirs_after) <= 1 and len(dirs_before) >= 2
    rank_dropped = rank_after < rank_before
    sig6a = dirs_collapsed and rank_dropped
    owned_acts = verdicts_after.get(owned) == "act"
    sig6b = owned_acts and bool(dead) and all(verdicts_after[k] != "act"
                                             for k in dead)
    print(f"  sig 6a (cuts structure):     {'GREEN' if sig6a else 'RED'}  "
          f"directions {len(dirs_before)} -> {len(dirs_after)} (the clean collapse); "
          f"effective_rank {_f(rank_before)} -> {_f(rank_after)} (partial -- the kept")
    print(f"                                   slice is a veil-spread cloud, not a line)")
    print(f"  sig 6b (dead on complement):  {'GREEN' if sig6b else 'RED'}  "
          f"owned '{owned}' still acts; dead on {dead if dead else '[]'} "
          f"(act -> escalate on {len(dead)}/{len(acted_before)} formerly-acted bases)")
    overall = sig6a and sig6b
    print(f"\n  OVERALL: {'GREEN -- the field exhibits the §4 specialization/death '
                          'signature under this construction' if overall else 'RED (see above)'}")
    print("\n  HONEST CAVEAT: this is a test of the §4 READING, not a proof. The cut is")
    print("  SELECTION (reuses aligned_safe_slice); the literal §4.7 SVD-projection")
    print("  coarse-graining is a deferred variant. §4.7 stays unvetted either way.")

    print(f"\nartifacts (per-cell field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


if __name__ == "__main__":
    main()