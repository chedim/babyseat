# The dev organism — cells that graze the field

`membrane opencode` runs a **self-balancing population of curiosity-driven
cells** alongside the dev field (default 4; `--cells 0` disables). The
machinery is the world pool's (`membrane/pool.py`, proven in
`experiments/multi_*.py`), wired onto the dev surface: the cells **graze the
exemplar field** through per-cell veils, each committed to one **interest the
field actually grew**, and a background budger buds/retires them as the
field's variety changes.

## What a cell is

One `Cell` (membrane/cell.py) wraps its own tiny Membrane: its own field
file, audit log, veil seed, and curiosity. Every cell is **observer mode**
(the never-authors guardrail): it forwards nothing to the user, serves
nothing, never touches the live gate — it accumulates a labeled *perspective*
on the field. Its intake is curiosity-gated: it absorbs only exemplars that
are novel *to it* and aligned with its committed cone
(`strength/(1+strength)`, default 1.0 → cos > 0.5).

## The lifecycle — they grow, bud, and die on their own

- **Grow.** Each cell runs its own graze loop (`Cell.graze`): every field
  exemplar that is novel to the cell and inside its cone is absorbed through
  the cell's veil. Self-extinguishing — as the cone fills, the count drops
  toward zero. No embed calls: numpy on pre-computed vectors only.
- **Bud (reproduce).** `_bud_thread` in `membrane/opencode.py` (≈30 s cycle)
  reads the field's distinct experienced directions (`orient_dirs`,
  cos-separated) — the *interests the field actually grew* — caps them by the
  CPU budget (`round(cpu_target × 30)`, ~6 at the default), and calls
  `pool.balance`: **bud one cell per cycle** when the population is under the
  interest count, committing it to a direction not yet covered. A static
  field grows no new directions → no buds → self-limiting by construction.
- **Die.** `pool.balance` retires at most one cell per cycle when over
  target: a **collapsed** cell first (its committed direction no longer
  matches any current field direction — its interest vanished from the
  world), else the **lowest-free-energy** cell past the grace period (≥ 3
  grazes — fresh buds are never culled before they grow). Demotion drains
  free_energy (the disorder tax), so wrong lineages fade instead of
  reproducing — the stage-5.6 closed loop (multi_energy_loop.py, GREEN).
- **Persist.** Cell fields are `cell-field-N.json` beside the main field
  (gitignored runtime state) and are reloaded on boot (`fresh=False`): the
  population keeps growing across restarts.

## Guardrails (inherit from the pool design, held)

- Observer mode — a cell never authors an answer, never feeds the live chat
  path. It only *informs* later retrievals indirectly? **No — not even that:
  the dev cells are pure observers on their own fields.** The ONLY field
  that serves recall is the operator's (`rag-field.json`). The cells exist
  to grow perspectives + energy under the same economics as the world sim,
  and to be visible (below).
- A cell cannot feed itself (no deposit path) — the boundary is structural.
- At most one bud/retire per cycle (hysteresis) — no oscillation.

## Seeing them

The dev viewer (`/viewer`) renders each cell as a sprite among the exemplar
stars: **colored by its covered interest** (the palette index of the
direction it owns), full-size once mature (≥ 4 exemplars → a stable dominant
direction), small + neutral while budding. The hotbar's **cells** stat shows
`<population>/<mature>`. `/membrane/state` exposes `pool_cells`
(`n`, `free_energy`, `dir`, `mature`, `ready`).

## Knobs

| Flag | Default | Meaning |
|---|---|---|
| `--cells N` | 4 | seeds; population self-balances above this |
| `--cells-interval S` | 30 | graze + budger cadence (seconds) |
| `--cpu-target F` | 0.2 | population cap ≈ `round(F × 30)` |

Boot line: `cells: on (4 grazing, cap ~6)`. `--cells 0` returns the
single-Membrane behavior (one `is None` check per cycle).

## Why

The energy economy (trigger / seed / cost, stages 4-5.6) was proven in the
experiments but never wired into daily use — the dev field grew by
absorption and compaction only. This connects it: the field's exemplars are
the **environment**; the cells are the **organism** that grazes it; free
energy prices reproduction; the environment's variety (new interests) is
the only thing that opens the bud gate, and the environment's votes
(demotions) are the only thing that drains a wrong lineage. Selection
pressure stays external to the cell — the membrane never grades itself.