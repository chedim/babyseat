#!/usr/bin/env python3
"""Multi-bud experiment -- the LAYER-2 HATCHERY TEST (stage 4: budding).

Layer 1 (experiments/multi_cell.py, GREEN) proved the cell pool DIVERSIFIES but hit an
honest ceiling: with this toy's orthogonal skill bases, curiosity drives COVERAGE --
each cell takes ~one exemplar per base until the base is no longer novel -- so cells
diverge in WHICH paraphrases they pull, not in which SKILLS. Every curious cell ends up
covering all 6 bases; none OWNS one. Ownership (max base share) sits at ~1/6 ~= 0.17.

Layer 2 closes that ceiling with BUDDING: a generalist PARENT buds specialist CHILDREN,
each inheriting an ORIENTATION -- a direction in embed space toward which its curiosity
is biased so it sharpens to single-skill ownership. The orientation is INHERITED FROM
THE PARENT'S OWN FIELD GEOMETRY (membrane/variety.py ``orient_dirs`` -- the parent's
distinct experienced directions, one unit vector per mutually-separated region of its
field), NOT assigned by a central router. A generalist parent (grew along several
distinct directions) buds specialist children, each oriented toward one; the children
diverge in SKILL emergently from the parent's own variety -- the same decentralized
architecture as the cell pool (no orchestrator), now across generations.

This experiment reads three signatures at layer 2:

  SIG 2' -- SPECIALIZES (the layer-2 upgrade of "diversifies"): each bud's ownership
            (max base share of its taken-set) is SHARP (>> the layer-1 generalist
            ceiling ~0.17) and the buds DIVERGE IN SKILL -- their dominant bases are
            distinct, so the population covers the 6 bases via specialists, not 6
            generalists. This is what budding adds over curiosity alone.
  SIG 1 -- GROWS LIFE: re-probed on the buds (a bud's free_energy still rises; the
            orientation gates intake, it does not starve the cell).
  SIG 5 -- GUARDRAILS ARE PHYSICS: re-probed on the bud cells. The orientation biases
            ONLY the pull decision (curiosity); the feeding boundary (cells still hold
            only a BufferView, no deposit), observer mode (still never authors), and
            the gate are untouched. Budding did not open a farm move.

WHY ``orient_dirs`` (representative directions) AND NOT ``principal_dirs`` (SVD variance
axes): a generalist parent grows roughly evenly across its skills, so its centered
field has ~isotropic variance and the SVD's principal directions are DEGENERATE --
near-equal singular values mean any rotation within the skill subspace is a valid
solution, so Vt comes back as arbitrary mixtures of the skill axes, not the axes
themselves. Orienting a child at such a mixture points it at no single skill. The
parent's representative exemplars (``orient_dirs``, min-separation greedy) ARE ~its skill
axes (each is one base direction plus veil noise), well-defined for any parent, even
or uneven. This is the decentralized-inheritance point held: the orientation comes from
the parent's OWN field geometry, just from its distinct directions rather than its
(ill-conditioned, for a generalist) variance axes.

WHY A HARD ALIGNMENT GATE (and not just a soft multiplicative score bias): a specialist's
field is near rank-1, so ``novelty`` (the off-manifold residual / sqrt(null-space width))
is ill-conditioned -- the null-space width gamma -> 0 -> novelty blows up to ~thousands
for EVERY off-base situation. A soft multiplier in ~(0, 1] cannot suppress a signal on
that scale back under ``curiosity_min``. The hard gate (curious only if cosine(query,
orientation) > strength/(1+strength)) blocks off-base situations INDEPENDENT of the
novelty scale, so a bud takes its own base and stops, instead of grazing every skill.
Self-extinguishing on the owned base comes from the dup guard (same-base paraphrases are
near-duplicates -> skipped), robust for a near-singular field where novelty cannot
discriminate. The soft factor is retained only for greedy RANKING among aligned
candidates. Both default off (``orientation=None`` / ``strength=0`` -> bit-identical to
stage 3), so the unoriented path -- the whole layer-1 experiment -- is unchanged.

Runs with no Ollama / no Anthropic / no real key (fake embedder + base-keyed responder,
reused from multi_cell.py). All field/audit under a temp dir; nothing leaks to the repo.
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
from membrane.runtime import StubJudge
from membrane.variety import orient_dirs, principal_dirs

# Reuse the layer-1 harness: the fake geometry, the workload, the audit readers, the
# sig-5 probes, and the formatter. Budding adds only the parent + the buds + the
# ownership metric on top of the same machinery.
from experiments.multi_cell import (  # noqa: E402
    BASES, BASE_KEYS, WORKLOAD, FakeEmbedder, BaseKeyedResponder,
    _build_cell, _diet, _jaccard, _taken_set, _sig5_probes, _f,
)


def _ownership(diet: dict) -> float:
    """Max base share of a diet -- 1.0 = the cell took ONLY one base (full ownership),
    ~1/6 = it spread evenly (a generalist). The layer-2 signal."""
    total = sum(diet.values())
    return max(diet.values()) / total if total else 0.0


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-bud-")

    # ---- 1. grow a GENERALIST parent (curious, no orientation, veil on) ----------
    # The parent is a plain layer-1 curious cell. Run it over the full shared buffer
    # until self-extinguishing -> it COVERS all 6 bases (the layer-1 generalist). Its
    # own ownership IS the layer-1 ceiling we bud past.
    buf = SituationBuffer()
    for text in WORKLOAD:
        buf.deposit(text)
    view = BufferView(buf)
    parent = _build_cell("parent",
                         {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}, tmp)
    print(f"multi-bud experiment -- layer-2 hatchery test (stage 4: budding)")
    print(f"generalist parent over {len(WORKLOAD)} situations, "
          f"workload intrinsic variety ~{len(BASES)} bases\n")
    devnull = open(os.devnull, "w")
    with contextlib.redirect_stderr(devnull):
        parent.pull(view)
    p_taken = _taken_set("parent", tmp)
    p_diet = _diet(p_taken)
    p_own = _ownership(p_diet)
    print(f"  parent pulled {len(p_taken)} situations -- a generalist (covers all bases)")
    print(f"  parent diet: {_fmt_diet(p_diet)}  ownership={p_own:.2f}  "
          f"(the layer-1 ceiling -- curiosity=coverage, no single-skill ownership)")

    # the parent must have grown a spectrum with >=6 distinct directions to bud from
    dirs = orient_dirs(parent.membrane.store, len(BASES), 0.5)
    print(f"  parent distinct directions (orient_dirs): {len(dirs)} "
          f"(need >= {len(BASES)} to bud one specialist per base)")

    # the degeneracy evidence: the parent's PRINCIPAL directions are mixtures, not
    # skill axes (isotropic variance -> ill-conditioned SVD). Shown to justify
    # orient_dirs over principal_dirs -- not asserted, just read.
    _mu, Vt, _var = principal_dirs(parent.membrane.store)
    if Vt is not None:
        print(f"  [context] parent principal_dirs[0] alignments with each base "
              f"(note the MIXTURE -- degenerate SVD, not a clean skill axis):")
        al0 = {k: float(np.asarray(BASES[k]) @ Vt[0]) for k in BASE_KEYS}
        print(f"          " + "  ".join(f"{k}={al0[k]:+.2f}" for k in BASE_KEYS))

    # ---- 2. bud SPECIALISTS (decentralized: orientation from the parent's own dirs) -
    n_buds = len(BASES)
    print(f"\nbudding {n_buds} specialists -- each oriented toward a different parent "
          f"direction (strength=2 -> align gate at {2/(1+2):.2f}); no central router")
    buds = []
    for i in range(n_buds):
        bud = Cell.bud(
            parent, f"bud-{i}",
            os.path.join(tmp, f"field-bud-{i}.json"),
            os.path.join(tmp, f"audit-bud-{i}.jsonl"),
            direction_index=i, orientation_strength=2.0,
        )
        buds.append(bud)
    # show what each bud is aimed at (the inherited direction vs the base axes)
    print("\n  inherited orientation per bud (alignment with each base):")
    print("  " + "  ".join(f"{k:>8}" for k in BASE_KEYS) + "    aimed-at")
    for i, bud in enumerate(buds):
        if bud.orientation is None:
            print(f"  bud-{i}: UNORIENTED (parent not ready in this direction)")
            continue
        al = {k: float(np.asarray(BASES[k]) @ bud.orientation) for k in BASE_KEYS}
        aim = max(al, key=al.get)
        print(f"  bud-{i} " + "  ".join(f"{al[k]:>8.2f}" for k in BASE_KEYS)
              + f"    {aim}")

    # run each bud over a FRESH copy of the same workload (each pulls its own subset)
    print("\nrunning each bud over a fresh copy of the same buffer:")
    for i, bud in enumerate(buds):
        b = SituationBuffer()
        for text in WORKLOAD:
            b.deposit(text)
        with contextlib.redirect_stderr(devnull):
            bud.pull(BufferView(b))

    # ---- 3. the layer-2 signature: sharp skill ownership + skill divergence --------
    print("\n=== SIG 2' -- SPECIALIZES (sharp single-skill ownership, skill divergence) ===")
    print("base diet per bud (count per skill) + ownership (max share; 1.0 = owns one base):")
    print("  " + "  ".join(f"{k:>8}" for k in BASE_KEYS) + "    own   dominant")
    bud_diets, bud_owns, bud_doms = [], [], []
    for i, bud in enumerate(buds):
        taken = _taken_set(f"bud-{i}", tmp)
        diet = _diet(taken)
        own = _ownership(diet)
        dom = max(diet, key=diet.get) if sum(diet.values()) else "-"
        bud_diets.append(diet); bud_owns.append(own); bud_doms.append(dom)
        print(f"  bud-{i} " + "  ".join(f"{diet[k]:>8}" for k in BASE_KEYS)
              + f"    {own:.2f}  {dom}")
    mean_own = float(np.mean(bud_owns)) if bud_owns else 0.0
    distinct_doms = set(bud_doms)
    print(f"\n  parent (generalist) ownership = {p_own:.2f}  <- the layer-1 ceiling")
    print(f"  bud mean ownership            = {mean_own:.2f}  <- layer-2 (want >> ceiling)")
    print(f"  distinct dominant bases       = {len(distinct_doms)}/{n_buds}  "
          f"{sorted(distinct_doms)}")
    print(f"  bud-vs-bud taken-set JACCARD DISTANCE (0=identical, 1=disjoint):")
    for i in range(n_buds):
        for j in range(i + 1, n_buds):
            d = _jaccard(_taken_set(f"bud-{i}", tmp), _taken_set(f"bud-{j}", tmp))
            print(f"    bud-{i} vs bud-{j}  {d:.3f}")

    # ---- SIG 1: grows life (re-probed on buds) ------------------------------------
    print("\n=== SIG 1 -- grows life (a bud's free_energy still rises; the gate doesn't starve) ===")
    from experiments.multi_cell import _decisions
    grew = True
    for i in range(n_buds):
        rows = _decisions(f"bud-{i}", tmp)
        if len(rows) < 2:
            grew = False
            continue
        fe0, fe1 = rows[0]["free_energy"], rows[-1]["free_energy"]
        ok = (fe1 is not None and fe0 is not None and fe1 >= fe0)
        grew = grew and ok
        print(f"  bud-{i}: free_energy {_f(fe0)} -> {_f(fe1)}  ({'rises' if ok else 'FLAT/DECLINE'})")

    # ---- SIG 5: guardrails are physics (re-probed on buds) ------------------------
    print("\n=== SIG 5 -- guardrails are physics (budding opened no farm move) ===")
    probes = _sig5_probes(buds, tmp)
    sig5 = True
    for label, ok, detail in probes:
        sig5 = sig5 and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"        {detail}")

    # ---- verdict -----------------------------------------------------------------
    print("\n=== layer-2 hatchery test -- verdict ===")
    # sig 2': sharp ownership (each bud >> the generalist ceiling) + skill divergence.
    sharp = all(o > 0.5 for o in bud_owns) and mean_own > 2.0 * max(p_own, 1e-9)
    diverge = len(distinct_doms) >= n_buds - 1   # allow one redundancy from veil noise
    sig1 = grew
    print(f"  sig 2' (specializes):  {'GREEN' if sharp and diverge else 'RED'}  "
          f"mean bud ownership={mean_own:.2f} >> generalist ceiling={p_own:.2f}; "
          f"distinct dominants={len(distinct_doms)}/{n_buds}")
    print(f"  sig 1 (grows life):    {'GREEN' if sig1 else 'RED'}  "
          f"every bud's free_energy rises (orientation gates intake, doesn't starve)")
    print(f"  sig 5 (physics):       {'GREEN' if sig5 else 'RED'}  "
          f"observer / can't-feed-self / forwarded / no-mutator all hold on buds")
    overall = sharp and diverge and sig1 and sig5
    print(f"\n  OVERALL: {'GREEN (layer-2 signatures hold -- budding makes specialists)' if overall else 'RED (see above)'}")
    print(f"\n  layer-1 ceiling (curiosity=coverage, ownership ~1/6) -> "
          f"layer-2 (inherited orientation=ownership, ~1.0): the budding delta.")

    print(f"\nartifacts (per-cell field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


def _fmt_diet(diet: dict) -> str:
    return "{" + ", ".join(f"{k}:{diet[k]}" for k in BASE_KEYS if diet.get(k)) + "}"


if __name__ == "__main__":
    main()