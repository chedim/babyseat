#!/usr/bin/env python3
"""Multi-telemetry -- the QUANTITATIVE BENCHMARK (stage 5.5): records the energy
economy as a TIME SERIES so the budding economy is legible as CURVES, not a single
GREEN point.

Stage 5's ``multi_energy.py`` proved the economy in ONE bud cycle (trigger -> seed
-> cost -> re-bud) with hard asserts at a point in time. This harness runs the SAME
economy over a LONG workload in PHASES and records the thermodynamic state EVERY
step, then renders it as curves:

  free_energy  -- the field's safe-weighted variety (the "energy" that funds budding)
  threshold    -- last_bud_energy + bud_cost (the line free_energy must cross to bud)
  bud events   -- where free_energy crossed the threshold (the operator buds a child)
  eff_rank     -- the effective dimensionality (variety), free_energy's near-ceiling
  safe_frac    -- fraction of the field the environment did NOT vote down
  ownership    -- the parent's skill concentration (stays low: a generalist that buds
                  specialists, vs a child's near-1.0)

THE ECONOMY'S CLAIM IS A CURVE: free_energy accrues -> crosses the threshold -> a
bud fires and the threshold SNAPS UP to the current free_energy (the cost paid) ->
free_energy must grow bud_cost MORE to re-cross -> re-bud. And in a STATIC phase
budding CONVERGES TO ZERO: the residual veil-pumping may fire one last bud, but the
cell's curiosity stops taking re-deposits once their directions are absorbed
(membrane/curiosity.py's self-extinguishing property), so free_energy plateaus and the
TAIL has no buds and a flat field size. That dying-out is the self-limiting bound
(budding can't run away) shown as a time series, not an assertion. Three mechanisms
hold it: (1) the per-bud growth-bound (free_energy must grow bud_cost since the last
bud), (2) curiosity self-extinguishing (a static environment stops being taken), (3)
the D ceiling (free_energy <= eff_rank <= D, so total growth -- and total buds -- is
finite).

PHASES (the workload is staged so variety arrives in waves, then stops):
  A -- 3 bases (parse/deploy/test)           : F 0 -> ~4   (growth -> buds)
  B -- 3 new bases (debug/refactor/docs)     : F ~4 -> ~6  (growth -> buds)
  C -- novel-stem situations (hashed to NEW  : F ~6 -> ~7  (marginal growth -> 0-1 bud)
       directions, growing eff_rank toward D) :
  D -- STATIC: repeats phase C's situations  : F plateaus, budding converges to 0
       (a residual bud may fire, then curiosity self-extinguishes -> flat tail)

Budding is OPERATOR-triggered here (stage 5 scope: the live pool does NOT auto-bud).
The harness plays the operator: each step it checks ``ready_to_bud(bud_cost)`` and,
in a growth phase, buds a child when it fires (paying the cost -> advancing
``last_bud_energy``). ``bud_cost = 0.4 * min(F, max(D-F, floor))`` tracks headroom:
it shrinks as F approaches D, but the growth requirement (F must have grown bud_cost
since the last bud) is what holds the bound, so a saturated F still can't re-bud.

Reuses the multi_cell harness (``FakeEmbedder``/``BaseKeyedResponder``/``WORKLOAD``/
``base_of``/``_ownership``) and multi_energy's batch split. No Ollama / no Anthropic
/ no real key. All field/audit under a temp dir; nothing leaks to the repo.
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

from experiments.multi_cell import (  # noqa: E402
    BASES, BASE_KEYS, WORKLOAD, _build_cell, base_of,
)
from experiments.multi_bud import _ownership  # noqa: E402

_D = len(BASES[next(iter(BASES))])     # 8 -- eff_rank's hard ceiling

# The phase workload. A and B partition WORKLOAD by base (3 bases each); C introduces
# novel stems (NOT in multi_cell.STEMS -> FakeEmbedder hashes them to fresh directions,
# growing eff_rank past the 6 base axes toward D); D is the STATIC control -- it repeats
# C's situations verbatim, so curiosity self-extinguishes -> free_energy plateaus and
# budding converges to zero in the tail (a residual bud may fire first).
A_BASES = {"parse", "deploy", "test"}
B_BASES = {"debug", "refactor", "docs"}
NOVEL = [
    "review the open pr carefully",
    "audit the dependency tree",
    "lint the new module",
    "profile the slow request path",
    "scan the logs for warnings",
    "check the migration script",
    "inspect the dead-letter queue",
    "trace the failing request",
    "benchmark the hot loop",
    "format the generated docs",
]
PHASE_A = [t for t in WORKLOAD if base_of(t) in A_BASES]
PHASE_B = [t for t in WORKLOAD if base_of(t) in B_BASES]
PHASE_C = list(NOVEL)
PHASE_D = list(NOVEL) * 3      # STATIC: same situations x3 -> curiosity self-extinguishes

PHASES = [("A", PHASE_A, True), ("B", PHASE_B, True),
          ("C", PHASE_C, True), ("D", PHASE_D, False)]   # (label, situations, is_growth)

# The budding cost floor: bud_cost tracks 0.4*min(F, D-F) but never goes below this,
# so a saturated field near D still faces a real (if small) growth requirement.
_BUD_COST_FLOOR = 0.25


def _pull(cell, text):
    """Deposit ONE situation and have the cell pull it (stderr silenced: the re-ask
    demotion diagnostics are expected, not errors). Returns the count taken."""
    buf = SituationBuffer()
    buf.deposit(text)
    devnull = open(os.devnull, "w")
    with contextlib.redirect_stderr(devnull):
        return cell.pull(BufferView(buf))


def _full_field_ownership(cell):
    """The cell's skill concentration over its WHOLE field (not just the audit takes).
    ``_ownership`` wants a {base: count} diet; we count over every exemplar."""
    counts = {k: 0 for k in BASE_KEYS}
    for ex in cell.membrane.store.exemplars:
        b = base_of(ex.situation)
        if b is not None:
            counts[b] += 1
    return _ownership(counts) if sum(counts.values()) else 0.0


def _bestbase_cos(d):
    dn = np.asarray(d, dtype=float)
    dn = dn / (np.linalg.norm(dn) + 1e-12)
    return max(float(dn @ np.asarray(BASES[k], dtype=float)) for k in BASE_KEYS)


def _bud_cost(free_e):
    """Adaptive budding cost: 0.4 * the tighter of (current energy, headroom to D),
    with a floor so near-saturation still costs something. Shrinks as F -> D, but the
    GROWTH requirement (F must have grown bud_cost since the last bud) is what holds
    the bound -- a static F can't cross however small the cost."""
    return 0.4 * min(free_e, max(_D - free_e, _BUD_COST_FLOOR / 0.4))


def _sparkline(values, lo, hi):
    """Render ``values`` as a single-row unicode sparkline (8 levels). Two aligned
    sparkline rows (free_energy / threshold) read as overlaid curves, same column =
    same step."""
    spark = "▁▂▃▄▅▆▇█"
    if hi <= lo:
        return spark[0] * len(values)
    out = []
    for v in values:
        x = (v - lo) / (hi - lo)
        out.append(spark[max(0, min(7, int(max(0.0, min(0.999, x)) * 8)))])
    return "".join(out)


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="multi-telemetry-")
    print("multi-telemetry -- the quantitative benchmark (stage 5.5: energy economy as curves)")
    print(f"one generalist parent over {sum(len(p) for _, p, _ in PHASES)} situations in "
          f"{len(PHASES)} phases (A/B/C growth, D static). D={_D}. operator-triggered buds.\n")

    parent = _build_cell("parent",
                         {"nudge_sigma": 0.25, "nudge_seed": 1, "curiosity_on": True}, tmp)

    rows = []                 # per-step thermodynamic record
    bud_events = []           # step indices where a bud fired
    child_own = []            # each child's birth ownership (the specialists)
    step = 0

    for label, sits, is_growth in PHASES:
        for text in sits:
            _pull(parent, text)
            ts = thermo_state(parent.membrane.store)
            f = parent.energy()
            bc = _bud_cost(f)
            threshold = parent.last_bud_energy + bc
            ready = parent.ready_to_bud(bc)
            bud_here = False
            if ready:                       # bud in ALL phases (incl static): a static
                                        # phase may fire a residual bud, but budding
                                        # CONVERGES to zero as curiosity self-extinguishes
                dirs = orient_dirs(parent.membrane.store, len(BASES), 0.5)
                if dirs:
                    di = max(range(len(dirs)), key=lambda i: _bestbase_cos(dirs[i]))
                    ci = len(child_own)
                    Cell.bud(parent, f"c{ci}",
                             os.path.join(tmp, f"field-c{ci}.json"),
                             os.path.join(tmp, f"audit-c{ci}.jsonl"),
                             direction_index=di, orientation_strength=2.0,
                             seed_size=3, seed_align_min=0.7)
                    child_own.append(_full_field_ownership(parent))   # parent stays generalist
                    bud_here = True
                    bud_events.append(step)
            rows.append({
                "step": step, "phase": label, "n": ts["n"], "eff_rank": ts["eff_rank"],
                "safe_frac": ts["safe_frac"], "free_energy": f,
                "last_bud": parent.last_bud_energy, "threshold": threshold,
                "ready": ready, "children": len(child_own),
                "ownership": _full_field_ownership(parent),
            })
            step += 1

    # ---- 1. sampled time-series table ------------------------------------------
    print("=== time series (sampled every 4 steps; full series in the chart below) ===")
    print(f"{'step':>4} {'ph':>2} {'n':>3} {'eff_rank':>8} {'safe%':>5} "
          f"{'free_e':>7} {'last_bud':>8} {'thresh':>7} {'ready':>5} {'kids':>4} {'own':>5}")
    for r in rows[::4]:
        print(f"{r['step']:>4} {r['phase']:>2} {r['n']:>3} {r['eff_rank']:>8.3f} "
              f"{r['safe_frac']*100:>5.0f} {r['free_energy']:>7.3f} {r['last_bud']:>8.3f} "
              f"{r['threshold']:>7.3f} {str(r['ready']):>5} {r['children']:>4} "
              f"{r['ownership']:>5.2f}")
    # always show the last step
    if rows[-1]["step"] != rows[::4][-1]["step"]:
        r = rows[-1]
        print(f"{r['step']:>4} {r['phase']:>2} {r['n']:>3} {r['eff_rank']:>8.3f} "
              f"{r['safe_frac']*100:>5.0f} {r['free_energy']:>7.3f} {r['last_bud']:>8.3f} "
              f"{r['threshold']:>7.3f} {str(r['ready']):>5} {r['children']:>4} "
              f"{r['ownership']:>5.2f}")

    # ---- 2. the curves (sparklines, aligned by step) ---------------------------
    print("\n=== the economy as curves (each column = one step, left -> right) ===")
    n = len(rows)
    fe = [r["free_energy"] for r in rows]
    th = [r["threshold"] for r in rows]
    hi = max(max(fe), max(th)) * 1.05
    lo = 0.0
    print(f"  free_energy : {_sparkline(fe, lo, hi)}")
    print(f"  threshold   : {_sparkline(th, lo, hi)}")
    bud_row = "".join("B" if i in bud_events else "·" for i in range(n))
    print(f"  bud events : {bud_row}  ({len(bud_events)} buds)")
    phase_row = "".join(r["phase"] for r in rows)
    print(f"  phase      : {phase_row}")
    print(f"  (sparkline scale: 0.0 -> {_f(hi)} ; 'B' = a child was budded that step)")
    print("  read it as: free_energy accrues, crosses the threshold, a bud fires and")
    print("  the threshold SNAPS UP (the cost paid), then free_energy must re-grow to")
    print("  re-cross. Phase D (static) plateaus -> budding converges to zero in the")
    print("  tail (curiosity self-extinguishes, the field stops taking).")

    # ---- 3. phase summary + asserts (the curve's claims, checked) -------------
    print("\n=== phase summary (the economy's claims as numbers) ===")
    buds_per_phase = {p: 0 for p, _, _ in PHASES}
    for i in bud_events:
        buds_per_phase[rows[i]["phase"]] += 1
    all_green = True
    static_converged = True        # the D self-limiting proof: budding -> 0 in the tail
    for label, sits, is_growth in PHASES:
        pr = [r for r in rows if r["phase"] == label]
        f0, f1 = pr[0]["free_energy"], pr[-1]["free_energy"]
        nb = buds_per_phase[label]
        grew = f1 - f0
        kind = "growth" if is_growth else "STATIC"
        if label in ("A", "B"):
            # the two big growth phases: each must bud (reliable, big variety influx)
            ok = nb >= 1
            claim = ">=1 bud expected"
        elif label == "C":
            # the novel-stem batch is marginal (small eff_rank headroom near D): it
            # SOMETIMES buds. Report it; don't require it -- honest about the toy's
            # small dynamic range.
            ok = True
            claim = f"{nb} bud(s) (marginal novel-stem growth; may be 0)"
        else:   # D -- the self-limiting proof is in the TAIL
            # budding CONVERGES to zero: in the last 10 steps, 0 new buds and the
            # field size n barely grew (curiosity self-extinguished -> the cell stopped
            # taking re-deposits). A residual bud early in D is allowed; the claim is
            # that it dies out, not that it never fires.
            tail = pr[-10:]
            tail_buds = sum(1 for i in bud_events if rows[i]["phase"] == "D"
                            and rows[i]["step"] >= tail[0]["step"])
            n_grew = tail[-1]["n"] - tail[0]["n"]
            ok = (tail_buds == 0) and (n_grew <= 1)
            static_converged = static_converged and ok
            claim = (f"{nb} bud(s) total, but tail (last 10) buds={tail_buds} "
                     f"n_grew={n_grew} -> self-extinguished")
        all_green = all_green and ok
        print(f"  phase {label} ({kind:6}, {len(sits)} sits): F {_f(f0)} -> {_f(f1)} "
              f"(grew {_f(grew):>6}), {nb} bud(s) -- {claim} -> {'OK' if ok else 'FAIL'}")

    # the cost is paid at every bud: last_bud snaps to free_energy at the bud step
    cost_paid = all(abs(rows[i]["last_bud"] - rows[i]["free_energy"]) < 1e-9
                    for i in bud_events)
    print(f"\n  cost paid at every bud (last_bud == free_energy at the bud step): "
          f"{cost_paid}")
    # the parent stays a generalist (low ownership) -- it buds specialists, doesn't become one
    gen_own = rows[-1]["ownership"]
    print(f"  parent stays generalist (final ownership {_f(gen_own)}, << 1.0): "
          f"{gen_own < 0.35}")
    print(f"  children budded: {len(child_own)}  (each a specialist at birth; the parent")
    print(f"    keeps its own field -- budding costs a LEDGER entry, not the parent's variety)")

    print("\n=== verdict ===")
    print(f"  growth phases bud (A,B), static phase budding converges to 0 (D tail): "
          f"{'GREEN' if all_green and cost_paid and static_converged else 'RED'}")
    print("  -> the energy economy is a self-limiting curve: accrual -> bud -> cost ->")
    print("     re-growth -> re-bud, and under a static environment budding CONVERGES to")
    print("     zero (curiosity self-extinguishes, the field stops taking). Stage 5's")
    print("     single-point asserts (multi_energy.py) are now a time series.")

    print(f"\nartifacts (per-cell field+audit): {tmp}")
    print("(temp dir -- nothing is written to the repo)")


def _f(x: float) -> str:
    return f"{x:.3f}"


if __name__ == "__main__":
    main()