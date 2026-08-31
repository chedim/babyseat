# The global field — a commons across membranes

Status: **slices 1–4 built** (operator promote + recall top-up; ledger shards
+ consensus promotion; scope axis + residency drift + suggestions; peers +
live ask). Flag: `--global-field [PATH]` (default
`~/.membrane/global-field.json`), kill switch `--no-global`.
Tests: `python -m tests.test_global_field` (11 checks, no pytest needed).

## The principle

Local fields are the actor ("no central router" — a single oracle is rank-1,
one perspective, cannot absorb variety). The commons is a **read-mostly file
layer**, not a server: each membrane stays sovereign, auto-joins by path
convention, and the operator's hand is still the only authority that writes
it. The field's own thermodynamics lift one level up: variety that exists in
one repo is *repo variety*; variety that reproduces across repos is
*operator-world variety* and earns the commons.

## What one membrane knows about another: `owner` / `home`

- `owner` — the field-id where a memory was BORN (provenance: who vouches for
  the text).
- `home` — the field that currently MASTERs it (who to ask; where it is
  exercised).
- field-id = 12-hex hash of the resolved field path. Derived, not configured.

## How global learns (three write paths, increasing trust)

1. **Operator's hand (built).** `membrane: remember globally: X`
   embeds X and writes the commons now; `membrane: promote #N` promotes local
   exemplar #N. Pre-gate, audited (`promote-global` / `forget-global`), with
   the `[membrane-intercept]` marker. Idempotent (text dedup). A second field
   promoting the same text adds its field-id to `votes` — consensus evidence.
2. **Consensus promotion (built).** Each field appends safe
   operator-notes + compact-notes (never raw tool traffic) to its OWN ledger
   shard `~/.membrane/ledger/<fid>.jsonl` — disjoint append-only files,
   lock-free by construction. The same note (cosine ≥ 0.95) from
   **≥ 2 distinct fields** auto-promotes with provenance on the next
   maintenance pass (a 120s daemon clock; also runs at boot via the first
   promote). This is the local demotion logic inverted: a re-ask votes
   against, reappearance across contexts votes for. `merge_ledgers()` is the
   deterministic merge; promote/forget also append/retract in the shard, so
   the evidence trail never outlives the note.
3. **The scope axis (built, suggest-only).** Two centroids (`c_global` from
   commons notes, `c_project` from clearly-local traffic) define an axis
   `c_global − c_project`; local notes near the global end park PENDING
   suggestions in `~/.membrane/scope-suggestions.jsonl` (surfaces on the
   120s clock when the local field grew ≥ 8). Review with
   `membrane: suggest`; the human ratifies via `membrane: promote #N`
   (which marks the suggestion resolved) or dismisses via
   `membrane: suggest drop <slot>`. Nothing auto-promotes without the hand.

**Rebalancing = residency drift (built):** a globally-promoted memory that
keeps earning recalls in field B but sits unused at birthplace A drifts
`home` to B (uses evidence accumulates on every top-up; ≥ 3 more uses than
the home field's count moves it); a commons copy the operator forgets at
home propagates a demotion vote to every replica (the ledger retract is the
slice-2 form: proposals die with the note). Promotion is a vote; residency
is a track record (Lloyd on memories — homes follow use, not birth).

## Coordination (no election, no leader, by design)

The commons is a **file**, not a process — there is nothing to elect.

- **Boot:** resolve field-id → open the file if it exists; missing file =
  empty commons; first promote creates it (mkdir + atomic save). Joining is
  opening.
- **Reads:** stat-mtime check per read; re-read only on external change
  (one cheap stat/turn; file is small by design).
- **Writes:** atomic all the way (temp + `os.replace`).
  - Built (slice 1): no locks on the commons itself; writes are
    operator-rare, and `promote` refreshes from disk first (mtime check) so a
    stale in-memory copy dedup-misses instead of clobbering a sibling's note.
  - Built (slice 2): consensus writes merge from disjoint ledger
    shards — nobody but the merge touches the commons file, the merge is
    deterministic given the same shard bytes (concurrent merges converge), and
    each field only ever appends ITS OWN shard (lock-free by construction).
- **Crash:** mid-promote crash leaves the old commons intact (atomic
  rename). A dead peer's memories survive in its `field.json` (readable
  snapshot); a live ask to a crashed peer fails → escalation falls through
  to the model (degrade toward the safe direction, the same covenant as
  judge-error → ACCEPT). Built (slice 4): self-registration
  (`~/.membrane/peers.jsonl`, 60s heartbeats, stale entries expire at
  PEER_STALE_S) routes around ghosts; `/membrane/ask` serves a peer's LOCAL
  kNN (read-only, capture-junk excluded, sim-floored) and an escalation turn
  asks up to 4 live peers with a 5s timeout — any hit rides along as
  `[membrane-intercept]` context, any failure silently degrades to the
  model path.

## Recall contract

Local kNN first (RAG block, unchanged). Commons top-up after it: max 2
memories, cosine ≥ 0.55 (`--global-sim-min`), MMR-deduped against local
picks (near-tie → local wins), provenance-tagged
`[global · owned by <fid> · sim]`. Never learned, never demoted — read-only.
The recall intercept (`membrane: query the field for …`) surfaces the same
top-up tags.

## Blast radius / failure modes

- Manifold mixing is bounded because the commons is **curated notes only**
  (operator/cortex provenance), never raw tool traffic.
- `membrane: forget globally …` is scoped to the commons; local forget never
  crosses the boundary. `forget everything` does not touch the commons.
- `--no-global` is the total kill switch; without the flag NOTHING changes
  (the layer is off by default).
- Secrets: the commons is operator-curated; the operator choosing to promote
  a secret is an explicit act, audited, forgettable by index.

## Slices

| slice | what | status |
|---|---|---|
| 1 | operator promote + recall top-up, `owner/home/votes` fields | **built** |
| 2 | ledger shards + consensus promotion + voter-independence weighting | **built** (votes counted per distinct field-id; full centroid weighting deferred — needs peer centroids to be more than ceremony) |
| 3 | scope axis + residency drift + review-queue suggestions | **built** (suggest-only; the hand ratifies) |
| 4 | `peers.jsonl` + live `/membrane/ask` | **built** (live ask = context injection on escalations; no auto-handoff) |

Voter independence (built, cheap form): two near-clone repos voting the same
memory is one opinion twice — the merge counts DISTINCT field-ids, not raw
entries, so a clone contributes one vote. Full centroid-similarity weighting
is deferred until peer centroids exist (slice 4's registry knows the peers;
the weighting is a dot product away when it earns its keep).