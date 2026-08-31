#!/usr/bin/env python3
"""Multi-organism experiment -- the REVERSE-OF-BUDDING RESURRECTION TEST (stage 6, part 2).

Phase 1 (experiments/multi_differentiate.py, GREEN) built ``Cell.differentiate`` and
showed the DEATH: a STANDALONE differentiated specialist cuts its field to one
direction and locks the hard alignment gate, so it ACTS on its owned base and
ESCALATES on its orthogonal complement (act -> escalate on the bases it used to
cover). A standalone specialist is dead on the complement -- the §4 "specialist
alone is dead" prediction.

This experiment reads the RESURRECTION: the organism -- a POPULATION of
differentiated specialists, one per base -- covers what no single one can. The
§4 prediction is "alive only composed": the part gives up its variety (dies on
its complement) so the WHOLE carries the variety none individually has --
"organization is the tax, socialized."

THE ORGANISM IS NOT A ROUTER. The original plan proposed a ``RoutingView`` (a
per-cell ``BufferView`` subclass filtering the shared buffer) and flagged a
"load-bearing veil problem" an external router would face. Both were
over-engineering, exposed by the question "why not one shared view?":

  - Differentiated specialists SELF-ROUTE via their own curiosity gates: an
    off-cone situation is not ``_aligned`` -> not ``curious`` (curiosity.py:127:
    ``curious`` ALSO requires ``_aligned``) -> not pulled. Each specialist pulls
    only its cone from the shared buffer. The architecture's "cell pulls, buffer
    stays passive" philosophy (buffer.py:6, pool.py:15) delivers the composition.
  - A per-cell ``RoutingView`` is a central allocator in disguise -- the exact
    thing buffer.py:16 warns against. Not built.
  - The veil problem dissolves: each cell veils a query under ITS OWN veil and
    compares against ITS OWN veiled orientation -- self-consistent per cell, no
    cross-veil cosine. The re-embed-in-raw / ``nudge_sigma=0`` fork was a solution
    to a problem the shared view doesn't have.

So Phase 2 adds NO library code (no ``RoutingView``, no ``CellPool`` change, no
``buffer.py`` change). The organism is a population; the resurrection is measured
by coverage (the union of act-verdicts). The "router" in this file is a TEST-TIME
argmax over the specialists' FIELD-SIM (the gate's nearest-neighbour cosine to
stored exemplars) -- a measurement that confirms the field-sim dispatch signal
identifies the acting specialist. (NOT orientation-alignment: that is the curiosity
PULL bias, stochastic per call, and the wrong single-shot dispatch signal -- see
section 5.) Not a production object.

THE TEST:
  1. Grow a generalist (curious, veil on) over the shared buffer -> 6 orient_dirs.
  2. Build 6 differentiated specialists, one per base: bud a child toward parent
     direction i (forward op), pull a FRESH shared buffer so it grows only its own
     cone (~5 dense even paraphrases), then differentiate (reverse op). Each owns a
     different base. Verify distinct ownership (6/6).
  3. Per specialist, per base: the gate ``decide`` verdict (act/escalate) and the
     alignment gate (_aligned). Show the DEATH per specialist: acts on 1 (owned),
     escalates 5 (the complement).
  4. ORGANISM coverage = for each base, does SOME specialist act? Union = all 6.
     RESURRECTION: organism (6) > max single specialist (1); the 5 each specialist
     is dead on are covered by its siblings.
  5. Dispatch signal: for each base, argmax FIELD-SIM (the gate's nearest-neighbour
     cosine to stored exemplars) over the specialists picks the one that ACTS on it
     -- the robust dispatch signal (NOT orientation-alignment, the curiosity pull
     bias, stochastic per call; see section 5).

HONEST CAVEAT (held, as in Phase 1): this is a test of the §4 READING, not a
proof. The cut is SELECTION (reuses aligned_safe_slice); the literal §4.7
SVD-projection coarse-graining is a deferred variant. §4.7 stays unvetted either
way. The calibrations (sim_min=0.70, gate_strength=1.0) are the toy's D=8 /
0.25-veil regime; the real 1024-d mxbai regime keeps 0.85/4.0.

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
from membrane.variety import orient_dirs, thermo_state

# Reuse the layer-1/2 harness: the fake geometry, the workload, the cell builder,
# the ownership metric, and the formatter. The organism adds only the population
# + the coverage measurement on top of the same machinery.
from experiments.multi_cell import (  # noqa: E402
    BASES, BASE_KEYS, WORKLOAD, FakeEmbedder, BaseKeyedResponder, _build_cell, _f,
)
from experiments.multi_bud import _ownership  # noqa: E402
# Reuse Phase 1's per-cell gate verdict (act/escalate) and alignment-gate verdict
# (allow/block) -- the same probes the death test used, now run across a population.
from experiments.multi_differentiate import (  # noqa: E402
    PROBES, _gate_verdict, _aligned_verdict,
)


def _owned_base(cell: Cell) -> str:
    """Which base a differentiated specialist OWNS -- argmax alignment of its
    committed orientation to the BASES axes. The orientation came from the cell's
    own field geometry (``orient_dirs``), so this identifies the base, not assigns
    it."""
    al = {k: float(np.asarray(BASES[k]) @ cell.orientation) for k in BASE_KEYS}
    return max(al, key=al.get)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-org-")
    print("multi-organism experiment -- the reverse-of-budding resurrection test")
    print("(stage 6, part 2: a population of differentiated specialists -- the organism)\n")

    # ---- 1. grow a GENERALIST (curious, veil on) over the shared buffer --------
    # One generalist gives the 6 orient_dirs we bud toward. Each specialist is a
    # bud (forward op, inherits parent direction i) that pulls a FRESH shared buffer
    # to grow its own cone, then differentiates (reverse op) -- so each owns a
    # different base.
    buf = SituationBuffer()
    for text in WORKLOAD:
        buf.deposit(text)
    view = BufferView(buf)
    seed_cfg = {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}
    devnull = open(os.devnull, "w")

    # ---- 1. grow a GENERALIST parent (curious, veil on) over the shared buffer --
    # The parent's own orient_dirs are the specialization axes the buds inherit.
    parent = _build_cell("parent", seed_cfg, tmp)
    # TOY CALIBRATION (honest, inherited from the Phase-1 death test): set the
    # parent's sim_min=0.70 BEFORE budding so the buds INHERIT 0.70 (the gate
    # default; the D=8/0.25-veil regime puts same-base cosine ~0.69, so 0.85 is
    # too strict for a specialist to act on its own base). gate_strength=1.0
    # (threshold 0.5; owned ~0.69 vs off ~0.1 separates cleanly). The real 1024-d
    # regime keeps 0.85/4.0.
    parent.membrane.sim_min = 0.70
    with contextlib.redirect_stderr(devnull):
        parent.pull(view)
    dirs = orient_dirs(parent.membrane.store, len(BASES), 0.5)
    print(f"parent grew {len(parent.membrane.store)} exemplars; "
          f"distinct directions (orient_dirs) = {len(dirs)} "
          f"(need {len(BASES)} to bud one specialist per base)")

    # ---- 2. bud 6 specialists, each pulls its OWN cone, then DIFFERENTIATE -----
    # The construction is the forward op then its reverse, in the same machinery:
    #   bud  (forward) -- a blank child inherits an orientation toward parent
    #                     direction i, then pulls the shared buffer, growing ONLY
    #                     its cone (~5 paraphrases of base i: dense and EVEN, vs an
    #                     identical-generalist cut which inherits the parent's
    #                     UNEVEN per-base coverage and can leave a 2-exemplar cone).
    #   differentiate (reverse) -- cuts the grown cone to its aligned slice and
    #                     locks the hard alignment gate.
    # Each specialist ends with a DENSE field of its owned base (~5 exemplars, not
    # the parent's sparse 2 for an under-covered base) so its gate ACTS on its
    # owned base robustly. The organism is the population; coverage is the union.
    print(f"\nbudding {len(BASES)} specialists (each inherits a parent direction, pulls "
          f"its own cone, then differentiates -- sim_min=0.70, gate_strength=1.0):")
    specs: list[Cell] = []
    owned: list[str] = []
    for i in range(len(BASES)):
        bud = Cell.bud(parent, f"spec-{i}",
                       os.path.join(tmp, f"field-spec-{i}.json"),
                       os.path.join(tmp, f"audit-spec-{i}.jsonl"),
                       direction_index=i, orientation_strength=2.0)
        # the bud boots blank + oriented; pull a FRESH shared buffer so it grows
        # its own cone (only base-i paraphrases clear its alignment gate).
        fresh = SituationBuffer()
        for text in WORKLOAD:
            fresh.deposit(text)
        with contextlib.redirect_stderr(devnull):
            bud.pull(BufferView(fresh))
        orient = bud.differentiate(direction_index=0, gate_strength=1.0)
        if orient is None:
            print(f"  spec-{i}: NOT READY (bud grew too few separable directions) -- RED")
            print(f"\nartifacts: {tmp}")
            return
        ob = _owned_base(bud)
        specs.append(bud)
        owned.append(ob)
        al_next = sorted((float(np.asarray(BASES[k]) @ bud.orientation) for k in BASE_KEYS
                          if k != ob), reverse=True)[0]
        print(f"  spec-{i} -> owns '{ob}'  "
              f"(orientation alignment {_f(float(np.asarray(BASES[ob]) @ bud.orientation))} "
              f"vs next {_f(al_next)}; field cut to {len(bud.membrane.store)} exemplars)")

    distinct = set(owned)
    print(f"\ndistinct owned bases = {len(distinct)}/{len(BASES)}  {sorted(distinct)}")
    if len(distinct) != len(BASES):
        print("  WARNING: ownership is not one-per-base (veil noise collapsed two "
              "specialists onto one base); coverage may be partial. Noted honestly.")

    # ---- 3. per-specialist per-base: the DEATH (acts on 1, escalates 5) --------
    # gate verdict (act/escalate) -- the same probe the death test used, across
    # the population. Each specialist should ACT on its owned base and ESCALATE on
    # the rest: the standalone death, per specialist.
    print("\ngate verdict (act/escalate) per specialist per base "
          "(each specialist acts on its OWNED base, escalates the rest):")
    print("  " + "  ".join(f"{k:>8}" for k in BASE_KEYS) + "    owned")
    verdicts: dict[tuple[int, str], str] = {}     # (spec_i, base) -> "act"/"escalate"
    sims: dict[tuple[int, str], float] = {}
    for i, c in enumerate(specs):
        row = []
        for k in BASE_KEYS:
            v, sim = _gate_verdict(c, PROBES[k])
            verdicts[(i, k)] = v
            sims[(i, k)] = sim
            row.append(f"{sim:.2f}" if v == "act" else " .")
        print(f"  spec-{i} " + "  ".join(f"{x:>8}" for x in row) + f"    {owned[i]}")

    # diagnostic: the owned-base sim per specialist (should be >= 0.70 to ACT) and
    # any cross-base sim that leaked >= 0.70, to read the toy's margins honestly.
    print("  [diag] owned-base sim (want >= 0.70) / max cross-base sim (want < 0.70):")
    for i in range(len(specs)):
        ob = owned[i]
        own_sim = sims[(i, ob)]
        cross = max(sims[(i, k)] for k in BASE_KEYS if k != ob)
        leak = [k for k in BASE_KEYS if k != ob and verdicts[(i, k)] == "act"]
        print(f"  spec-{i} ({ob:<8}): owned sim={own_sim:.2f} "
              f"{'ACT' if own_sim >= 0.70 else 'MISS'}; max cross={cross:.2f}"
              + (f"  LEAK to {leak}" if leak else ""))

    # ---- 4. ORGANISM coverage = union of act-verdicts (the RESURRECTION) -------
    # For each base, does SOME specialist act? The organism covers a base if any
    # of its specialists does. A standalone specialist covers only its owned base;
    # the organism covers all 6 -- the bases each specialist is dead on are
    # resurrected by its siblings.
    print("\norganism coverage (does SOME specialist ACT on each base?) "
          "vs a standalone specialist:")
    org_cov: list[str] = []
    for k in BASE_KEYS:
        actors = [i for i in range(len(specs)) if verdicts[(i, k)] == "act"]
        org_cov.append("act" if actors else "esc")
        who = f"via spec-{actors[0]} ({owned[actors[0]]})" if actors else "NONE"
        print(f"  {k:<10} -> {'COVERED' if actors else 'DEAD'}  {who}")
    org_n = sum(1 for k in BASE_KEYS if org_cov[BASE_KEYS.index(k)] == "act")

    # the standalone baseline: the worst (min) and best (max) single-specialist
    # coverage -- any one specialist alone is dead on all but its owned base.
    single_cov = [sum(1 for k in BASE_KEYS if verdicts[(i, k)] == "act")
                  for i in range(len(specs))]
    print(f"\n  organism covers              {org_n}/{len(BASES)} bases")
    print(f"  single specialist covers     {single_cov}  (each acts on its owned base only)")
    print(f"  max single-specialist cover  {max(single_cov)}/{len(BASES)}")

    # ---- 5. dispatch signal: which specialist covers each base -----------------
    # The dispatch signal a serving router would use is NOT orientation-alignment.
    # The honest finding: curiosity's ``_aligned`` (cosine to the orientation) is
    # the PULL-time self-routing bias, but it is STOCHASTIC PER CALL -- ``_situation_vec``
    # re-veils with a fresh RNG draw every call (cell.py:408: "the veil is stochastic
    # per call"), so a fresh probe's orientation-alignment is HIGH-VARIANCE in this
    # toy's D=8/0.25-veil regime (e.g. deploy lands at align 0.20 under its owner).
    # It self-routes correctly OVER THE WORKLOAD (each bud pulled its base -- many
    # paraphrases average the cone), but a single-shot alignment probe is the wrong
    # test of it.
    #
    # The ROBUST dispatch/coverage signal is the gate's FIELD-SIM: nearest-neighbour
    # cosine to the stored (fixed) exemplars. That is what ``decide`` uses (novelty
    # is OFF for these rank-1 fields), and it is what the resurrection measures. So
    # the router = "which specialist's field covers this query" = argmax field-sim =
    # the specialist that ACTS. For each base, the argmax-field-sim specialist is the
    # owner, and it acts.
    print("\ndispatch signal (argmax FIELD-SIM, the gate's primary cosine -- robust; "
          "vs orientation-alignment, the stochastic pull-time bias):")
    routed_ok = 0
    for k in BASE_KEYS:
        # robust: argmax over specialists of the gate's nearest-neighbour sim
        best_i = int(max(range(len(specs)), key=lambda i: sims[(i, k)]))
        acts = verdicts[(best_i, k)] == "act"
        routed_ok += int(acts and owned[best_i] == k)
        # contrast: the stochastic orientation-alignment (curiosity's pull bias) --
        # how many specialists does a fresh-probe alignment clear at all? (noisy,
        # non-selective: not the dispatch signal.)
        align_clears = sum(1 for c in specs if _aligned_verdict(c, PROBES[k]))
        owner_align = float(specs[best_i]._situation_vec(PROBES[k]) @ specs[best_i].orientation)
        print(f"  {k:<10} -> argmax field-sim = spec-{best_i} ({owned[best_i]})  "
              f"sim={_f(sims[(best_i, k)])}  -> {'ACTS (covered)' if acts else 'ESCALATES'}  "
              f"[owner's orient-align={_f(owner_align)} (stochastic); "
              f"align clears {align_clears}/{len(specs)} cells]")
    print(f"  (orientation-alignment is the curiosity PULL bias -- stochastic per call;")
    print(f"   the gate field-sim dispatched correctly for {routed_ok}/{len(BASES)}. "
          f"The resurrection uses the gate.)")

    # ---- 6. verdict -----------------------------------------------------------
    print("\n=== reverse-of-budding resurrection test -- verdict ===")
    # resurrection: organism coverage strictly exceeds the best single specialist,
    # AND covers every base (the complement each specialist is dead on is covered).
    sig_r = (org_n > max(single_cov)) and (org_n == len(BASES))
    # death per specialist re-affirmed: each acts on exactly its owned base (1).
    death = all(single_cov[i] == 1 for i in range(len(specs)))
    # dispatch: argmax field-sim picks the acting owner for every base.
    sig_route = routed_ok == len(BASES)
    print(f"  death (per specialist):   {'GREEN' if death else 'RED'}  "
          f"each specialist acts on 1 base (owned), escalates {len(BASES)-1} "
          f"(standalone specialist is dead on its complement)")
    print(f"  resurrection (organism):  {'GREEN' if sig_r else 'RED'}  "
          f"organism covers {org_n}/{len(BASES)} > max single {max(single_cov)}; "
          f"the complement each is dead on is covered by its siblings")
    print(f"  dispatch (field-sim):     {'GREEN' if sig_route else 'RED'}  "
          f"argmax field-sim picks the acting owner for "
          f"{routed_ok}/{len(BASES)} bases")
    overall = sig_r and death and sig_route
    if overall:
        print("\n  OVERALL: GREEN -- the organism resurrects what the standalone "
              "specialist is dead on (alive only composed)")
    else:
        print("\n  OVERALL: RED (see above)")
    print("\n  HONEST CAVEAT: a test of the §4 READING, not a proof. The organism is a")
    print("  population on a SHARED view (self-routing via curiosity); no RoutingView,")
    print("  no router object, no veil re-embedding. The cut is SELECTION; the §4.7")
    print("  SVD-projection coarse-graining is a deferred variant. §4.7 stays unvetted.")
    print("  The dispatch signal is the gate's field-sim, NOT orientation-alignment")
    print("  (the curiosity pull bias, stochastic per call in this toy -- a fresh probe")
    print("  misroutes; it self-routes over the workload, not on a single shot).")

    print(f"\nartifacts (per-specialist field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


if __name__ == "__main__":
    main()