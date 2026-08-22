#!/usr/bin/env python3
"""Multi-energy-loop -- the CLOSED LOOP (stage 5.6): energy as the SELECTOR.

Stage 5 (multi_energy) proved the economy in one bud cycle; stage 5.5 (multi_telemetry)
proved it as a self-limiting curve. BOTH grew only SAFE variety -- the responder is
always correct, so there are never wrong absorbs, never demotions. They showed the
GROWTH/STABLE half of the energy economy. The owed test (scorecard C5, deferred as
"the closed loop") is the DECAY half: does a demoted cell's free_energy drop actually
BLOCK ready_to_bud, removing wrong lineages from reproduction? Is energy a SELECTOR,
not just a dashboard?

THE THESIS (the nursery requires a nurser): without an environment sourcing selection
pressure on quality, wrong buds are immortal (no demotion -> no drain -> no fade). With
it, a wrong absorb -> the env re-asks (votes against) -> demotion -> the exemplar moves
from the safe term to the disorder tax in free_energy -> free_energy drains -> the cell
falls below the budding threshold -> it stops reproducing (the lineage FADES). The
nurser's culling is the only drain on wrong-path energy; remove it and a wrong entry sits
at full weight forever (a cosine cache: the A/B's cosine condition replays wrong 3/3).

THE LOOP, IN ONE RUNNING SYSTEM (not two spliced probes): two cells over the SAME growth
stream, identical config (same veil, same bud_cost), differing ONLY in whether the
environment culls:
  OFF -- no nurser. Grows freely -> free_energy rises -> buds -> re-grows -> re-buds.
         Wrong/neutral variety is IMMORTAL (the cosine-cache equivalent: nothing ever
         demotes, so nothing ever drains, so nothing ever fades).
  ON  -- the nurser culls. Grows -> buds; then the env demotes its safe exemplars (the
         re-ask vote) -> free_energy drains -> ready_to_bud flips off -> FADES (no re-bud).
         The nurser keeps culling each new growth wave -> the cell can't re-cross the
         threshold -> it stays faded while OFF keeps budding.
Then the TWO-SIDED boundary (the other authority), in the ON cell:
  RESCUE  -- the model CONTESTS the demotions (votes for) -> safe restored -> free_energy
             recovers -> ready_to_bud flips on -> RE-BUD. The model gets one veto: a
             wrongly-demoted slot is rescued.
  OUT-VOTE -- the env re-asks AGAIN (reask=2 > contest=1) -> net votes >= threshold ->
              demotion STANDS -> safe stays False -> free_energy drains -> FADE permanent.
              The environment keeps the final say (the existing contest() boundary rule).

The demotion/contest are the PUBLIC two-authority mutators (add_reask/add_contest +
recompute_safe) -- the SAME core runtime._demote/contest() use in the live path, minus
the judge/audit/print. The experiment PLAYS THE ENVIRONMENT (the nurser) by casting
re-ask votes, exactly as multi_telemetry plays the OPERATOR (triggers buds). The cell
itself never demotes its own exemplars (it can't -- demotion is the env's authority,
external to the cell); the selection pressure is sourced outside, which is the point of
the thesis. The cell's own re-ask detection (_detect_reask, the cosine trigger) is already
proven elsewhere (A/B + live smoke); this test exercises the EFFECT a demotion has on
the energy economy, invoked via its public core.

Reuses multi_telemetry's growth stream + sparkline + bud_cost + _pull, multi_cell's cell
builder. No Ollama / no key. All field/audit under a temp dir; nothing leaks to the repo.
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane.cell import Cell
from membrane.variety import thermo_state, orient_dirs

from experiments.multi_cell import BASES, _build_cell  # noqa: E402
from experiments.multi_telemetry import (  # noqa: E402
    _pull, _bud_cost, _sparkline, _bestbase_cos, PHASE_A, PHASE_B, _D,
)


# ---------------------------------------------------------------- the two authorities
def _demote(cell, idx):
    """The ENVIRONMENT votes AGAINST exemplar ``idx`` (a re-ask): add_reask + recompute_safe.
    The public two-authority core that ``runtime._demote`` uses (minus the judge/audit).
    The experiment plays the nurser -- the cell itself has no demote path."""
    s = cell.membrane.store
    s.add_reask(idx)
    s.exemplars[idx].recompute_safe(cell.membrane.demote_threshold)
    s.save(cell.membrane.field_path)


def _contest_vote(cell, idx):
    """The MODEL votes FOR exemplar ``idx`` (a contest): add_contest + recompute_safe.
    The public two-authority core that ``runtime.contest`` uses (minus the audit)."""
    s = cell.membrane.store
    s.add_contest(idx)
    s.exemplars[idx].recompute_safe(cell.membrane.demote_threshold)
    s.save(cell.membrane.field_path)


def _cull_all(cell):
    """The nurser re-asks EVERY currently-safe exemplar (votes against the lot).
    Returns the count demoted. The strongest selection: nothing the cell absorbed this
    wave survives, so it can accrue no safe variety -> it can never re-cross the bud
    threshold -> it fades while the no-nurser cell keeps budding."""
    idxs = [i for i, e in enumerate(cell.membrane.store.exemplars) if e.safe]
    for i in idxs:
        _demote(cell, i)
    return len(idxs)


def _rescue_all(cell):
    """The model contests EVERY currently-unsafe (demoted) exemplar (votes for the lot).
    One contest cancels one re-ask -> safe restored. Returns the count rescued."""
    idxs = [i for i, e in enumerate(cell.membrane.store.exemplars) if not e.safe]
    for i in idxs:
        _contest_vote(cell, i)
    return len(idxs)


def _outvote_all(cell):
    """The env re-asks EVERY exemplar AGAIN (reask=2 vs contest=1) -> net votes >=
    threshold -> demotion STANDS (the environment wins the action). Returns the count."""
    idxs = list(range(len(cell.membrane.store.exemplars)))
    for i in idxs:
        _demote(cell, i)
    return len(idxs)


# ---------------------------------------------------------------- state + budding
def _state(cell):
    """Per-cell thermodynamic snapshot: free_energy, the budding threshold, readiness,
    safe_frac, field size. The threshold is last_bud + bud_cost (per-cell: bud_cost
    tracks each cell's own energy, so OFF and ON have separate thresholds)."""
    f = cell.energy()
    bc = _bud_cost(f)
    ts = thermo_state(cell.membrane.store)
    return {"fe": f, "thr": cell.last_bud_energy + bc, "ready": cell.ready_to_bud(bc),
            "bc": bc, "safe_frac": ts["safe_frac"], "n": len(cell.membrane.store),
            "n_safe": sum(1 for e in cell.membrane.store.exemplars if e.safe)}


def _maybe_bud(cell, tmp, tag, counter):
    """If ready, bud a child (advance ``last_bud_energy`` = the cost paid) and return
    True. The child is a throwaway written under tmp; the PARENT's ledger + energy is
    what the loop measures. Decentralized: the cell measures its own field."""
    bc = _bud_cost(cell.energy())
    if not cell.ready_to_bud(bc):
        return False
    dirs = orient_dirs(cell.membrane.store, len(BASES), 0.5)
    if not dirs:
        return False
    di = max(range(len(dirs)), key=lambda i: _bestbase_cos(dirs[i]))
    ci = counter[0]
    counter[0] += 1
    Cell.bud(cell, f"{tag}-c{ci}",
             os.path.join(tmp, f"field-{tag}-c{ci}.json"),
             os.path.join(tmp, f"audit-{tag}-c{ci}.jsonl"),
             direction_index=di, orientation_strength=2.0,
             seed_size=3, seed_align_min=0.7)
    return True


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-energy-loop-")
    print("multi-energy-loop -- the CLOSED LOOP (stage 5.6: energy as the selector)")
    print("two cells over the SAME growth stream, identical veil/bud_cost, differing ONLY")
    print("in whether the environment culls. D=%d.\n" % _D)

    cfg = {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}
    off = _build_cell("off", cfg, tmp)     # no nurser: nothing ever demotes -> immortal
    on = _build_cell("on", cfg, tmp)       # the nurser culls -> drain -> fade

    off_ctr, on_ctr = [0], [0]
    off_buds = on_buds = 0
    rows = []           # per-step record (step, phase, event, off_state, on_state,
                        # off_bud, on_bud)
    step = 0

    def step_record(phase, event, off_bud, on_bud):
        nonlocal step
        rows.append({"step": step, "phase": phase, "event": event,
                     "off": _state(off), "on": _state(on),
                     "off_bud": off_bud, "on_bud": on_bud})
        step += 1

    # ---- Phase A: grow both, no culling (both reach readiness and bud) -----------
    for t in PHASE_A:
        _pull(off, t); _pull(on, t)
        ob = _maybe_bud(off, tmp, "off", off_ctr)
        nb = _maybe_bud(on, tmp, "on", on_ctr)
        off_buds += ob; on_buds += nb
        step_record("A", "grow", ob, nb)
    off_buds_a = off_buds
    on_buds_a = on_buds
    fe_on_pre_cull = on.energy()
    n_demoted = _cull_all(on)          # the nurser arrives: re-ask every ON absorb
    nb = _maybe_bud(on, tmp, "on", on_ctr); on_buds += nb
    step_record("A", f"cull(-{n_demoted})", False, nb)
    fe_on_post_cull = on.energy()

    # ---- Phase B: grow both; ON culls every new wave (fades), OFF grows free -----
    off_buds_b = on_buds_b = 0
    for t in PHASE_B:
        _pull(off, t); _pull(on, t)
        _cull_all(on)                 # the nurser re-asks every ON absorb this wave
        ob = _maybe_bud(off, tmp, "off", off_ctr)
        nb = _maybe_bud(on, tmp, "on", on_ctr)
        off_buds += ob; on_buds += nb
        off_buds_b += ob; on_buds_b += nb
        step_record("B", "grow+cull", ob, nb)

    # ---- RESCUE: the model contests every demotion (one veto) -> restore ---------
    fe_on_pre_rescue = on.energy()      # the drained value BEFORE the rescue
    n_rescued = _rescue_all(on)
    nb = _maybe_bud(on, tmp, "on", on_ctr)
    on_buds += nb
    on_buds_rescue = nb
    step_record("|", f"rescue(+{n_rescued})", False, nb)
    # give the rescued cell a little fresh growth to confirm it can re-bud on its own
    for t in PHASE_B[:3]:
        _pull(on, t)
        nb = _maybe_bud(on, tmp, "on", on_ctr)
        on_buds += nb; on_buds_rescue += nb
        step_record("|", "rescue-grow", False, nb)
    fe_on_post_rescue = on.energy()

    # ---- OUT-VOTE: the env re-asks again (reask=2 > contest=1) -> demotion stands -
    n_outvoted = _outvote_all(on)
    all_unsafe = all(not e.safe for e in on.membrane.store.exemplars)
    nb = _maybe_bud(on, tmp, "on", on_ctr); on_buds += nb
    step_record("|", f"out-vote(-{n_outvoted})", False, nb)
    fe_on_post_outvote = on.energy()

    # ---- STATIC tail: a few repeats, no growth -> both converge ------------------
    for t in PHASE_B[-3:]:
        _pull(off, t); _pull(on, t)
        _cull_all(on)
        ob = _maybe_bud(off, tmp, "off", off_ctr)
        nb = _maybe_bud(on, tmp, "on", on_ctr)
        off_buds += ob; on_buds += nb
        step_record("D", "static+cull", ob, nb)

    # ---- 1. the time series -----------------------------------------------------
    print("=== time series (free_energy / threshold / ready per step) ===")
    print(f"{'step':>4} {'ph':>2} {'event':<16} "
          f"{'OFF fe':>7}{' thr':>7}{' rd':>4}{' bud':>4}  "
          f"{'ON fe':>7}{' thr':>7}{' rd':>4}{' bud':>4} {'sf':>4}")
    for r in rows:
        o, n = r["off"], r["on"]
        print(f"{r['step']:>4} {r['phase']:>2} {r['event']:<16} "
              f"{o['fe']:>7.2f}{o['thr']:>7.2f}{str(o['ready'])[:1].upper():>4}"
              f"{int(r['off_bud']):>4}  "
              f"{n['fe']:>7.2f}{n['thr']:>7.2f}{str(n['ready'])[:1].upper():>4}"
              f"{int(r['on_bud']):>4} {n['safe_frac']*100:>3.0f}%")

    # ---- 2. the curves (sparklines, aligned by step) ----------------------------
    print("\n=== the selector as curves (each column = one step, left -> right) ===")
    n = len(rows)
    off_fe = [r["off"]["fe"] for r in rows]
    on_fe = [r["on"]["fe"] for r in rows]
    off_thr = [r["off"]["thr"] for r in rows]
    on_thr = [r["on"]["thr"] for r in rows]
    hi = max(max(off_fe), max(on_fe), max(off_thr), max(on_thr)) * 1.05
    lo = min(0.0, min(on_fe)) * 1.1
    print(f"  OFF free_e : {_sparkline(off_fe, lo, hi)}")
    print(f"  OFF thresh  : {_sparkline(off_thr, lo, hi)}")
    print(f"  ON  free_e : {_sparkline(on_fe, lo, hi)}")
    print(f"  ON  thresh  : {_sparkline(on_thr, lo, hi)}")
    off_bud_row = "".join("B" if r["off_bud"] else "·" for r in rows)
    on_bud_row = "".join("B" if r["on_bud"] else "·" for r in rows)
    print(f"  OFF buds   : {off_bud_row}  ({off_buds} total)")
    print(f"  ON  buds   : {on_bud_row}  ({on_buds} total)")
    phase_row = "".join(r["phase"] for r in rows)
    print(f"  phase      : {phase_row}")
    print(f"  (sparkline scale: {_f(lo)} -> {_f(hi)} ; 'B' = a child budded that step)")
    print("  read it as: OFF (no nurser) free_energy grows and re-crosses its threshold")
    print("  -> repeated budding (immortal). ON (nurser) free_energy is held BELOW its")
    print("  threshold by culling -> no budding in phase B (faded). RESCUE: the model")
    print("  contests -> ON free_energy recovers -> re-buds. OUT-VOTE: the env re-asks")
    print("  again -> demotion stands -> ON drains back -> faded for good.")

    # ---- 3. the verdict (the closed loop's claims, checked) ---------------------
    print("\n=== verdict (the closed loop -- is energy a selector?) ===")
    fails = []

    def check(cond, msg):
        print(("  [PASS] " if cond else "  [FAIL] ") + msg)
        if not cond:
            fails.append(msg)

    # (1) SELECTOR / nurser necessity: OFF buds in B (re-grows -> re-crosses -> re-bud);
    #     ON buds 0 in B (culled below threshold -> faded). The ONLY difference is the
    #     nurser. Without it (OFF) the lineage is immortal; with it (ON) it fades.
    check(off_buds_b >= 1,
          f"OFF buds in phase B (no nurser -> re-grows -> re-buds): {off_buds_b}")
    check(on_buds_b == 0,
          f"ON buds 0 in phase B (nurser culls -> below threshold -> faded): {on_buds_b}")
    check(off_buds > on_buds,
          f"OFF reproduces more than ON overall ({off_buds} vs {on_buds} buds) -- "
          f"the nurser priced wrongness out of reproduction")

    # (2) DRAIN: the cull drops ON's free_energy (demotion -> disorder tax -> drain).
    check(fe_on_post_cull < fe_on_pre_cull,
          f"the cull DRAINS ON free_energy ({_f(fe_on_pre_cull)} -> {_f(fe_on_post_cull)})")
    check(on.energy() < 0 or _state(on)["safe_frac"] < 0.5,
          f"after sustained culling ON is mostly unsafe (safe_frac "
          f"{_state(on)['safe_frac']*100:.0f}%) / drained -- wrongness is mortal")

    # (3) RESCUE (the two-sided boundary): the model's contest restores safe ->
    #     free_energy recovers -> ON re-buds. The model gets one veto.
    check(fe_on_post_rescue > fe_on_pre_rescue,
          f"contest RESTORES ON free_energy ({_f(fe_on_pre_rescue)} -> "
          f"{_f(fe_on_post_rescue)}) -- the model's veto")
    check(on_buds_rescue >= 1,
          f"ON re-buds after rescue (the wrongly-demoted lineage is rescued): "
          f"{on_buds_rescue}")

    # (4) OUT-VOTE (the env keeps the final say): re-ask again (reask=2 > contest=1)
    #     -> net >= threshold -> demotion STANDS -> all unsafe -> drains -> faded.
    check(all_unsafe,
          f"after the env re-asks again, ALL ON exemplars are unsafe (reask=2 out-votes "
          f"contest=1 -> demotion stands)")
    check(fe_on_post_outvote < fe_on_post_rescue,
          f"the out-vote DRAINS ON again ({_f(fe_on_post_rescue)} -> "
          f"{_f(fe_on_post_outvote)}) -> faded permanently")

    # (5) the boundary is two-sided: free_energy tracks the two authorities. OFF (env
    #     absent) never drains -> immortal; ON (env present) drains -> fades, but the
    #     model's contest recovers it. Neither side has a monopoly.
    check(off_buds_a >= 1 and on_buds_a >= 1,
          f"both cells bud in phase A before culling ({off_buds_a}/{on_buds_a}) -- "
          f"identical until the nurser arrives")

    print()
    if fails:
        print(f"CLOSED LOOP: RED ({len(fails)} claim(s) failed)")
        for f in fails:
            print("  - " + f)
        sys.exit(1)
    print("CLOSED LOOP: GREEN -- energy is a SELECTOR, not just a dashboard.")
    print("  demotion -> free_energy drain -> ready_to_bud off -> the wrong lineage")
    print("  FADES (stops reproducing). The nurser (env re-ask) sources the gradient;")
    print("  without it (OFF) wrong/neutral variety is IMMORTAL (a cosine cache). The")
    print("  boundary is two-sided: the model's contest rescues a wrongly-demoted slot,")
    print("  but the env re-asks again -> out-votes -> demotion stands (env wins the")
    print("  action). Wrongness is priced out of reproduction; this is the closed half of")
    print("  the A/B's energy-fade probe, now one running system.")

    print(f"\nartifacts (per-cell field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


def _f(x: float) -> str:
    return f"{x:.3f}"


if __name__ == "__main__":
    main()