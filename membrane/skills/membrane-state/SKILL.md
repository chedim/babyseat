---
name: membrane-state
description: Inspect membrane's own live state (field size, safe ratio, effective rank, free energy, compactor, gate knobs). Use when the user asks about membrane's memory health, stats, or state (e.g. "field state", "memory stats", "what's your free energy", "compactor status").
---

# Membrane state

This skill is served BY MEMBRANE, not by opencode and not by the model: the
proxy intercepts the turn and computes the answer from the live field — no
model call, and the turn is NOT learned into the field.

## How to ask

Send a chat turn (no tools) phrased as a direct state ask:

    field state
    membrane state
    memory stats
    show the compactor status
    what is your effective rank / free energy

Membrane answers internally with: backend + embed model, field composition
(safe/unsafe, compacted, notes), thermodynamics (eff_rank, rank_ratio,
free_energy, safe_frac, spectrum_entropy), gate knobs, retrieval settings,
and compactor counters.

## Asking inside your own turns (thought intercept)

You do NOT need a dedicated chat turn: if your assistant-turn text contains
a canonical ask ("field state"), the proxy detects it pre-gate and injects
the answer into your NEXT turn as a clearly-marked
`[membrane-intercept]` system block. Do not answer state questions by
curl/tool-then-paste — the proxy handles canonical asks in your own prose.

## Reading the numbers

- `free_energy` rising slowly with real traffic = the field is growing useful
  variety. Flat = no new learning. Sudden jumps = entropy flood or junk burst.
- `safe_frac` dropping = memories being demoted (bad answers or aggressive
  re-asks) — worth a look at recent turns.
- Compactor `notes` > 0 with rising `retired` = clusters being distilled; the
  retrieval surface is compacting while raw detail stays in the store.

## NEVER do these (model-collapse hazards)

- Do not ask membrane state questions through the model path (curl, tool call,
  then paste) — the intercept only fires on direct chat phrasings, and a
  pasted report would be learnable.
- Do not restate the state report back into a later turn as user content.