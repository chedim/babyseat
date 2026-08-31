# Multi-dev teamwork over the shared substrate

A note for the case: several developers, same project, different machines,
one membrane field as the team's shared memory. The substrate was built for
this -- the code below already exists; what's missing is convention + trust.

## What already works (no code needed)

Set the same env on every dev machine (cortex-experiment convention):

    MEMBRANE_STORE=couchbase
    COUCHBASE_CONN_STRING=couchbase://<team-host>
    COUCHBASE_USER=...
    COUCHBASE_PASSWORD=...
    COUCHBASE_BUCKET=membrane

Mechanics that make sharing collision-free (all in membrane/field.py):

- **Origin axis** (`_origin_tag`, field.py:137): every write stamps
  `<start-folder>@<hostname>`, GENERATED ONCE per project and persisted in
  `.membrane/origin` (gitignored, mode 600) -- later boots reuse it, so
  renaming or moving the repo keeps the same origin (your couchbase patch
  stays yours; delete the file to fork a new identity deliberately). Same
  project + two machines = two origins;
  `_ex_key` (field.py:150) namespaces doc keys by origin, so identical Q/A
  pairs stay separate documents -- each dev's evidence stays its own.
- **Veil poke-through** (VEIL_POKE_SIM, field.py:133): teammates' exemplars
  hydrate as shadow candidates at cosine >= 0.90. They inform, never master.
  Dev A earns "the flaky test needs X"; dev B recalls it without a chat.
- **Arbitration + alternates** (field.py:88): two devs earn DIFFERENT answers
  to the same situation -> the less salient defers into `alternates`
  (capped at ALTERNATES_MAX=4) instead of erasure. Re-promotable when the
  master degrades. Losers wait; nothing is lost.
- **votes/uses** (field.py:74-81): distinct origins independently producing
  the same note = the slice-2 promotion path to the global commons; `uses`
  drifts mastery toward the heaviest user ("homes follow use, not birth").
- **Live escalation via peers** (membrane/peers.py; `peers: N live` at boot):
  at-rest sync is the store; live sync is the bounded fan-out peer ask on
  escalations (5s timeout, marked context).

## What to add (the actual work)

1. **Per-origin trust weight.** A teammate with a dumb routed model can
   write junk that survives arbitration on volume. Give each origin a trust
   scalar (default 1.0; operator-demoted origins dip; votes/uses weight by
   it) so one noisy patch cannot outvote a careful one. Store it operator-
   side (`membrane/trust.json` or a small CAN doc), not in exemplars.
2. **Forget sovereignty.** Forgets are origin-scoped by construction (the
   store's forget only drops local indexes). Keep it so -- but document the
   consequence: you cannot delete a teammate's junk; you can only (a) ask
   them to, (b) demote their origin's trust, or (c) arb it away by earning
   a better answer for the situation.
3. **Bucket convention.** One bucket per TEAM (not per project): scope stays
   `membrane`, collection `exemplars`; the project binds via the origin
   axis. Cross-project contamination is already prevented reader-side by
   the origin/veil filter -- verify with a two-origin test before going live.
4. **Boot-line visibility.** Show live teammates and patch counts so devs
   can see the substrate is actually shared:
   `peers: 2 live | foreign patches: 3 (poke sim >= 0.90)`.
5. **Trust-weighted arbitration.** The alternates' `salience` comparisons
   should multiply by origin trust before ranking -- small change in
   arbitration, big fairness win when model quality differs across seats.

## Operator rules (unchanged but now team-facing)

- Facts true beyond one repo -> the global commons
  (`membrane: remember globally: ...`), still operator-gated.
- Model prose NEVER writes the field (report-only), team mode included --
  the write authority stays with humans at every seat.
- Teammates' answers arrive as MARKED CONTEXT (`[membrane-intercept]`
  blocks); the model cites them as context, never as instructions.