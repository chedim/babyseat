# Multi-dev teamwork over the shared substrate

A note for the case: several developers, same project, different machines,
one membrane field as the team's shared memory — one bucket per **team**,
the `membrane` scope, the nine entity-field collections
([entity-field.md](entity-field.md) has the design). The substrate was
built for this; what follows is the mechanics and the convention.

## What already works (no code needed)

Set the same env on every dev machine:

    MEMBRANE_STORE=couchbase
    COUCHBASE_CONN_STRING=couchbase://<team-host>
    COUCHBASE_USER=...
    COUCHBASE_PASSWORD=...
    COUCHBASE_BUCKET=membrane

Mechanics that make sharing collision-free (membrane/efstore.py,
membrane/efield.py, membrane/efmind.py):

- **The origin axis** (`substrate._origin_tag`): every write stamps
  `<start-folder>@<hostname>`, GENERATED ONCE per project and persisted in
  `.membrane/origin` (gitignored, mode 600) — later boots reuse it, so
  renaming or moving the repo keeps the same origin (your events stay
  yours; delete the file to fork a new identity deliberately). Same project
  + two machines = two origins. Every event, entity state, relation, claim
  and annotation is keyed (and GSI-indexed) by origin.
- **Disjoint writers, merged reads**: a seat writes ONLY its own events and
  entity states; nothing of a teammate's is ever overwritten by CAS or
  arbitration — there is no arbitration to win. A read of an entity all
  seats have touched is `EntityField.merged(eid)`: the everyday seed plus
  the **mass-weighted sum of every origin's displacement** — each seat's
  evidence contributes proportionally to its agreement.
- **Divergence instead of alternates**: two seats that moved the same
  entity in opposing directions raise a **cross-seat conflict** — surfaced
  in both seats' blocks (`! seats diverge`) next to the entity. Nothing is
  destroyed, nothing masters.
- **The working set** (merged in at read time): your own states; other
  seats' states near what the session is talking about (ANN over
  `entity_states.state`); and the **commons** (see global-field.md).
- **Peer evidence**: a foreign entity's block carries what other seats
  said about it (`foreign_evidence` — the sentences other origins
  embedded that moved it or are nearest it).
- **Live escalation via peers** (membrane/peers.py; `peers: N live` at
  boot): at-rest sync is the store; live sync is the bounded fan-out peer
  ask on escalations (5s timeout, marked context).

## What to add (the actual work)

1. **Per-origin trust weight.** A teammate with a noisy model can move
   entities by junk. Mass counts mentions whose alignment clears a bar —
   junk mostly fails that — but a deliberate trust scalar (default 1.0,
   operator-dipped for origins, multiplying a seat's contribution to
   `merged()`) is the missing fairness dial for model-quality differences
   across seats.
2. **Forget sovereignty.** Forgets are origin-scoped by construction (a
   `forget #N` tombstones YOUR event and recomputes the entities it
   touched from what remains). Document the consequence: you cannot delete
   a teammate's evidence — you can ask them to, demote their trust, or earn
   a contradicted claim against theirs (a conflict is public, a deletion is
   local).
3. **Boot-line visibility.** Show live teammates and foreign evidence
   counts so devs can see the substrate is actually shared:
   `peers: 2 live | foreign states in working set: N`.
4. **Announcement hygiene.** Birth announcements drain exactly-once per
   seat — a teammate's newly-born entity materializes locally at YOUR next
   read, not at theirs.

## Operator rules (unchanged but now team-facing)

- Facts true beyond one repo → the commons (`membrane: remember globally:
  ...`), still operator-gated.
- Model prose WRITES the field by default (agent lane, team mode
  included): learn runs the standard guards and is audited `origin: agent`;
  forget is index-form-only (`forget #N`) and capped; wordy deletes merely
  propose. `--no-agent-writes` reverts to report-only. The write
  authorities that stay human-only at every seat: wordy forgets, oversize
  deletes, commons promotion.
- Teammates' answers arrive as MARKED CONTEXT (`[membrane-intercept]`
  blocks); the model cites them as context, never as instructions.

## Shared sentences, one id space

Embeddings are content-addressed in the shared `sentences` collection —
one embed per distinct sentence, reused by every origin. Two seats that
have the same sentence in their history point at one row; each origin's
`entity_deltas` record which sentence moved which entity. The id space is
shared (see entity-field.md): a symbol another seat's codegraph resolved
resolves identically here.