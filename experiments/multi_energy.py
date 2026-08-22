#!/usr/bin/env python3
"""Multi-energy experiment -- the LAYER-3 HATCHERY TEST (stage 5: energy economy).

Stage 4 (experiments/multi_bud.py, GREEN) proved a generalist buds specialists via an
inherited ORIENTATION -- but left three holes: WHEN a cell buds (no trigger), WHAT the
child carries (it started BLANK), and WHY auto-budding can't run away (no cost). Stage 5
closes all three with the ENERGY ECONOMY:

  TRIGGER -- a cell buds when its own ``free_energy`` (safe-weighted variety) has grown
             ``bud_cost`` past its last bud (``Cell.ready_to_bud``). Decentralized: the
             cell measures its own field; no scheduler decides who buds.
  SEED    -- the child inherits a slice of the parent's SAFE exemplars aligned with its
             orientation (``Cell.bud`` ``seed_size>0``). Born competent, not blank. This
             is SIG 3 (byproduct-not-extraction): the seed is the parent's proven-safe
             FORWARDS (a byproduct of serving), NOT a harvest of the shared buffer.
  COST    -- budding spends the parent's accrued free_energy (``last_bud_energy`` set);
             the parent must grow ``bud_cost`` MORE to bud again. A static environment
             grows zero -> zero further buds -> no runaway. Self-limiting.

SCOPE: economy only -- the operator/experiment triggers buds; the live pool does NOT
auto-bud (``pool.py``/``proxy.py`` untouched). Autonomous reproduction is a later stage.

Reads five signatures at layer 3:

  SIG (trigger)    -- ``ready_to_bud`` flips True on accrued safe variety; False right
                      after a bud (the cost paid); True again after new growth.
  SIG 3            -- byproduct-not-extraction: the seed ⊆ the parent's safe field; every
                      child exemplar's action is a forward (``do-<base>``), never a
                      harvest; every situation is inherited-or-pulled (no phantom).
  SIG 1            -- grows life: the seeded child is BORN with ``free_energy > 0`` (the
                      seed = inherited life, vs a blank child's 0) and still GROWS on top
                      of it (energy rises from birth).
  SIG 2'           -- specializes: the seeded child still owns one skill (the orientation
                      gate + the clean same-base seed keep it a specialist).
  SIG 5            -- guardrails are physics: seeding opened no farm move (observer /
                      can't-feed-self / no-mutator all hold on the seeded child).

THE BATCH SPLIT (the one structural refinement over the plan): the parent sees only 3 of
the 6 bases first (batch A) -> ``free_energy`` F0 ~ 4 with real growth headroom. It buds;
then batch B introduces the OTHER 3 bases (new directions for the parent) + novel-stem
situations (stems not in ``multi_cell.STEMS`` -> ``FakeEmbedder`` hashes them to fresh
directions) -> ``eff_rank`` grows ~4 -> ~6.2 -> ``ready_to_bud`` flips True again. Putting
all 6 bases in batch A saturates the parent near the D=8 ceiling (F0 ~ 6.1) and leaves too
little headroom to re-cross ``bud_cost``; the split makes the growth bound demonstrable
both ways (no re-bud without growth; re-bud after growth) within the toy's small dynamic
range. ``bud_cost = 0.4 * min(F0, D-F0)``: small enough to fire now and to leave re-bud
headroom, large enough (~37% of F0) to be a real barrier.

Reuses the multi_cell harness (``FakeEmbedder``/``BaseKeyedResponder``/``WORKLOAD``/...)
and multi_bud's ``_ownership``. Runs with no Ollama / no Anthropic / no real key. All
field/audit under a temp dir; nothing leaks to the repo.
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
from membrane.variety import aligned_safe_slice, orient_dirs

# Reuse the layer-1 harness: the fake geometry, the workload, the base resolver, the
# audit readers, the sig-5 probes, and the formatter. Stage 5 adds only the energy/seed/
# cost logic on top of the same machinery.
from experiments.multi_cell import (  # noqa: E402
    BASES, BASE_KEYS, WORKLOAD, FakeEmbedder, BaseKeyedResponder,
    _build_cell, _diet, _taken_set, _sig5_probes, _f, base_of,
)
from experiments.multi_bud import _ownership  # noqa: E402

# The embed dimension (8) -- ``free_energy``'s hard ceiling (eff_rank <= D).
_D = len(BASES[next(iter(BASES))])

# 3 bases the parent sees first (batch A) -- enough safe variety to bud, with real
# headroom (the other 3 bases + novel directions come in batch B and grow the parent
# past the bud). The split is the only structural refinement over the approved plan.
A_BASES = {"parse", "deploy", "test"}
B_BASES = {"debug", "refactor", "docs"}

# Novel-stem situations: stems NOT in multi_cell.STEMS -> FakeEmbedder hashes them to NEW
# directions in D=8, growing the parent's effective rank toward D (extra headroom past
# the 6 base axes). The growth-bound positive case rides mainly on the 3 held-back bases;
# the novel-stem shows growth beyond the intrinsic base count.
NOVEL_BATCH = [
    "review the open pr carefully",
    "audit the dependency tree",
    "lint the new module",
    "profile the slow request path",
    "scan the logs for warnings",
    "check the migration script",
]

BATCH_A = [t for t in WORKLOAD if base_of(t) in A_BASES]
BATCH_B = [t for t in WORKLOAD if base_of(t) in B_BASES] + NOVEL_BATCH


def _pull(cell, texts):
    """Deposit ``texts`` into a fresh buffer and have ``cell`` pull it (stderr silenced:
    the re-ask demotion diagnostics are expected, not errors). Returns the count taken."""
    buf = SituationBuffer()
    for t in texts:
        buf.deposit(t)
    devnull = open(os.devnull, "w")
    with contextlib.redirect_stderr(devnull):
        return cell.pull(BufferView(buf))


def _aligned_count(store, direction, align_min=0.7):
    """How many of ``store``'s SAFE exemplars align with ``direction`` (cosine >=
    ``align_min``) -- the available seed for a child oriented this way. Used to pick the
    parent's richest-grown direction for the seed demo (the OPERATOR chooses which of the
    parent's OWN directions to bud along; the orientation itself is the parent's geometry,
    never a central router)."""
    return len(aligned_safe_slice(store, np.asarray(direction, dtype=float), align_min, 999))


def _full_field_bases(cell):
    """Base diet over the cell's WHOLE field (seed + own growth), not just the audit
    takes. The seed (clean same-base) is part of the child's specialization, so ownership
    over the full field is the honest specialist measure -- and the robust one, since the
    clean seed dilutes any single off-base curiosity slip during growth."""
    counts = {k: 0 for k in BASE_KEYS}
    for ex in cell.membrane.store.exemplars:
        b = base_of(ex.situation)
        if b is not None:
            counts[b] += 1
    return counts


def _cosine(a, b):
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    return float(a @ b / ((np.linalg.norm(a) + 1e-12) * (np.linalg.norm(b) + 1e-12)))


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-energy-")
    print("multi-energy experiment -- layer-3 hatchery test (stage 5: energy economy)")
    print(f"parent sees {len(A_BASES)} bases first (batch A, {len(BATCH_A)} situations), buds,")
    print(f"then grows on the other {len(B_BASES)} bases + {len(NOVEL_BATCH)} novel-stem "
          f"(batch B, {len(BATCH_B)} situations). D={_D}.\n")

    # ---- 1. TRIGGER: grow the parent to budding readiness ---------------------
    parent = _build_cell("parent",
                         {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}, tmp)
    na = _pull(parent, BATCH_A)
    f0 = parent.energy()
    # bud_cost: 0.4 * the tighter of (current energy, headroom to D) -> fires now AND
    # leaves re-bud headroom. ~37% of F0: a real barrier, not a token one.
    bud_cost = 0.4 * min(f0, max(_D - f0, 0.0))
    print("=== TRIGGER -- grow to readiness (decentralized: the cell measures its own field) ===")
    print(f"  parent pulled {na} situations -> free_energy F0={_f(f0)}")
    print(f"  bud_cost = {_f(bud_cost)}  (0.4 * min(F0, D-F0) = 0.4 * min({_f(f0)}, {_f(max(_D-f0,0.0))}))")
    dirs = orient_dirs(parent.membrane.store, len(BASES), 0.5)
    print(f"  parent distinct directions: {len(dirs)} (need >=1 to orient a child)")
    trig = parent.ready_to_bud(bud_cost)
    print(f"  ready_to_bud(bud_cost) = {trig}  (F0 {_f(f0)} >= last_bud 0 + bud_cost {_f(bud_cost)}, and >=1 dir)")
    assert trig, "TRIGGER: parent should be ready to bud after accruing safe variety"
    print("  -> TRIGGER fires on accrued safe variety (free_energy crossed the threshold)")

    # pick the direction to bud along: prefer one whose aligned SAFE slice is SINGLE-BASE
    # (all aligned exemplars map to one skill) with >=2 exemplars -- a clean same-base
    # seed -- and among those pick the CLEANEST (highest cosine to the nearest BASE axis).
    # ``orient_dirs`` returns unit vectors of ACTUAL exemplars (one base + veil noise); with
    # nudge_sigma=0.25 a ~3-sigma nudge can land a base exemplar closer to a NEIGHBORING
    # base, so a "mixed" direction's slice spans two bases and seeds a child that owns
    # neither cleanly. Selecting a single-base slice sidesteps the veil artifact at the
    # SEED; a rarer residual (the child's GROWTH taking one cross-base situation, ~1/12
    # seeds, because curiosity's veil is re-sampled per call so the gate sees a different
    # realization than the stored vec) leaves ownership ~0.83 -- still a specialist (>0.5)
    # with all forwards intact (sig 3 holds). If no single-base slice has >=2, fall back to
    # the richest-by-count direction. The orientation is still the parent's own geometry;
    # this only chooses WHICH of the parent's grown directions to bud along.
    def _bestbase_cos(d):
        dn = np.asarray(d, dtype=float)
        dn = dn / (np.linalg.norm(dn) + 1e-12)
        return max(float(dn @ np.asarray(BASES[k], dtype=float)) for k in BASE_KEYS)
    def _slice_bases(store, d, align_min=0.7):
        from membrane.variety import aligned_safe_slice as _as
        sl = _as(store, np.asarray(d, dtype=float), align_min, 999)
        bset = {base_of(e.situation) for e in sl}
        return len(sl), bset
    counts = [_aligned_count(parent.membrane.store, d, 0.7) for d in dirs]
    slice_info = [_slice_bases(parent.membrane.store, d, 0.7) for d in dirs]
    pure = [i for i, (n, bset) in enumerate(slice_info)
            if len(bset) <= 1 and n >= 2]
    if pure:
        clean_idx = max(pure, key=lambda i: _bestbase_cos(dirs[i]))
    else:                                   # no clean single-base >=2 -> richest, then cleanest
        maxn = max(n for n, _ in slice_info)
        cand = [i for i, (n, _) in enumerate(slice_info) if n == maxn]
        clean_idx = max(cand, key=lambda i: _bestbase_cos(dirs[i]))
    rich_count = counts[clean_idx]
    print(f"  budding along the parent's cleanest single-base direction: index {clean_idx} "
          f"({rich_count} aligned safe exemplars @0.7 -> a strong, clean seed)")
    assert rich_count >= 2, f"need >=2 aligned exemplars for a live seed, got {rich_count}"

    # ---- 2. SEED: the child inherits the parent's safe byproduct ---------------
    print("\n=== SEED -- the child inherits the parent's safe byproduct (sig 3) ===")
    child = Cell.bud(parent, "child-0",
                     os.path.join(tmp, "field-child-0.json"),
                     os.path.join(tmp, "audit-child-0.jsonl"),
                     direction_index=clean_idx, orientation_strength=2.0,
                     seed_size=3, seed_align_min=0.7)
    seed_n = len(child.store)
    child_birth_energy = child.energy()
    seed_situations = {e.situation for e in child.membrane.store.exemplars}
    print(f"  child-0 born with {seed_n} seed exemplars (stage 4 starts at 0) "
          f"-> free_energy={_f(child_birth_energy)} (born competent, not blank)")
    assert seed_n > 0, "SEED: child should inherit a non-empty seed (stage 4 starts blank)"

    # SIG 3a: the seed IS the parent's safe field (byproduct), not a buffer copy; and it
    # is all-safe + aligned with the inherited orientation.
    parent_safe = {e.situation for e in parent.membrane.store.exemplars if e.safe}
    seed_in_parent = seed_situations <= parent_safe
    seed_all_safe = all(e.safe for e in child.membrane.store.exemplars)
    seed_aligned = all(_cosine(e.vec, child.orientation) >= 0.7 - 1e-9
                       for e in child.membrane.store.exemplars)
    print(f"  seed ⊆ parent's safe field: {seed_in_parent}  (byproduct, not a buffer harvest)")
    print(f"  seed all safe: {seed_all_safe}  |  seed all aligned w/ orientation: {seed_aligned}")
    assert seed_in_parent and seed_all_safe and seed_aligned, "SEED: byproduct/aligned/safe invariant"

    # ---- 3. COST / GROWTH BOUND -----------------------------------------------
    print("\n=== COST / GROWTH BOUND -- budding spends the parent's energy (no runaway) ===")
    print(f"  parent.last_bud_energy = {_f(parent.last_bud_energy)} (== F0 {_f(f0)}: the cost paid)")
    assert abs(parent.last_bud_energy - f0) < 1e-9, "cost: last_bud_energy should == F0"
    neg = parent.ready_to_bud(bud_cost)
    print(f"  ready_to_bud(bud_cost) right after bud = {neg}  "
          f"(F0 {_f(f0)} < F0+bud_cost {_f(f0+bud_cost)}: can't re-bud without growth)")
    assert not neg, "GROWTH BOUND (negative): parent must not re-bud immediately"

    print(f"  ...growing the parent over batch B ({len(BATCH_B)} situations: "
          f"{len(B_BASES)} new bases + {len(NOVEL_BATCH)} novel-stem)...")
    nb = _pull(parent, BATCH_B)
    f1 = parent.energy()
    pos = parent.ready_to_bud(bud_cost)
    print(f"  parent pulled {nb} more -> free_energy F0 {_f(f0)} -> F1 {_f(f1)}  (grew by {_f(f1-f0)})")
    print(f"  ready_to_bud(bud_cost) = {pos}  (F1 {_f(f1)} >= last_bud {_f(parent.last_bud_energy)} "
          f"+ bud_cost {_f(bud_cost)} = {_f(parent.last_bud_energy+bud_cost)})")
    assert f1 > f0, "GROWTH BOUND (positive): parent free_energy should rise with new variety"
    assert pos, "GROWTH BOUND (positive): parent should re-earn budding after growth"
    print("  -> self-limiting: budding needs NEW growth since the last bud; growth reopens it")

    # ---- 4. SIG 1 + SIG 2': the seeded child grows + owns ---------------------
    print("\n=== SIG 1 + 2' -- the seeded child grows life + owns its skill ===")
    print(f"  child-0 birth free_energy = {_f(child_birth_energy)}  "
          f"(the seed = inherited life; a stage-4 blank child starts at 0.0)")
    assert child_birth_energy > 0, "SIG 1: seeded child should be born with free_energy > 0"
    nc = _pull(child, WORKLOAD)
    child_after = child.energy()
    child_grew = len(child.store) - seed_n
    print(f"  child-0 pulled {nc} (grew by {child_grew} on top of the seed) "
          f"-> free_energy {_f(child_birth_energy)} -> {_f(child_after)}")
    assert child_grew > 0, "SIG 1: seeded child should still GROW on top of the seed"
    assert child_after > child_birth_energy, "SIG 1: child free_energy should rise from birth"
    own = _ownership(_full_field_bases(child))
    print(f"  child-0 full-field ownership = {_f(own)}  (>>0.21 generalist ceiling -> specialist)")
    assert own > 0.5, "SIG 2': seeded child should still own one skill"

    # ---- 5. SIG 3: byproduct-not-extraction on the GROWN child ---------------
    print("\n=== SIG 3 -- byproduct-not-extraction (the field is serving's byproduct, not a harvest) ===")
    # (a) every exemplar's action is a FORWARD (do-<base>), never its own situation / a buffer entry
    actions_are_forwards = all(e.action.startswith("do-") and e.action != e.situation
                               for e in child.membrane.store.exemplars)
    # (b) every exemplar's situation is either an inherited seed situation or one the child PULLED
    #     (audited as a decision) -- never a phantom harvest of a buffer entry it didn't serve.
    child_taken = _taken_set("child-0", tmp)
    pulled_or_seed = all(
        e.situation in seed_situations
        or (e.situation in WORKLOAD and WORKLOAD.index(e.situation) in child_taken)
        for e in child.membrane.store.exemplars)
    print(f"  every action is a forward (do-<base>, never a harvest): {actions_are_forwards}")
    print(f"  every situation is inherited-or-pulled (no phantom harvest): {pulled_or_seed}")
    assert actions_are_forwards and pulled_or_seed, "SIG 3: field must be byproduct of serving"

    # ---- 6. SIG 5: guardrails hold on the seeded child -----------------------
    print("\n=== SIG 5 -- guardrails are physics (seeding opened no farm move) ===")
    probes = _sig5_probes([child], tmp)
    sig5 = True
    for label, ok, detail in probes:
        sig5 = sig5 and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        print(f"        {detail}")

    # ---- verdict --------------------------------------------------------------
    print("\n=== layer-3 economy test -- verdict ===")
    sig_trig = trig and not neg and pos
    sig3 = seed_in_parent and seed_all_safe and seed_aligned and actions_are_forwards and pulled_or_seed
    sig1 = child_birth_energy > 0 and child_grew > 0 and child_after > child_birth_energy
    sig2p = own > 0.5
    print(f"  trigger (fires/no-rebud/rebud-after-growth): {'GREEN' if sig_trig else 'RED'}")
    print(f"  sig 3 (byproduct-not-extraction):           {'GREEN' if sig3 else 'RED'}")
    print(f"  sig 1 (born alive, then grows):              {'GREEN' if sig1 else 'RED'}")
    print(f"  sig 2' (specializes):                       {'GREEN' if sig2p else 'RED'}")
    print(f"  sig 5 (guardrails):                          {'GREEN' if sig5 else 'RED'}")
    overall = sig_trig and sig3 and sig1 and sig2p and sig5
    print(f"\n  OVERALL: {'GREEN (layer-3 economy holds -- trigger/seed/cost/byproduct)' if overall else 'RED (see above)'}")
    print("\n  stage 4 (budding: blank child + inherited orientation -> specialization) ->")
    print("  stage 5 (energy economy: trigger on safe variety + inherited safe seed +")
    print("           cost-bounded growth): the cell earns the right to reproduce, passes")
    print("           its proven-safe byproduct forward, and can't bud faster than it grows.")

    print(f"\nartifacts (per-cell field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


if __name__ == "__main__":
    main()