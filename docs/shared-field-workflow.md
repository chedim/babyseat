# The shared-field workflow: a worldwide distributed team on one membrane

How a team spread across time zones codes against ONE shared memory. This is
the operator manual: what exists, what to type, what to expect. Companion to
`docs/multi-dev-field.md` (the substrate plumbing) — this doc is the
PROCESS.

## The idea in one paragraph

Each seat runs membrane against its own coding agent (opencode, one entity
field per project). The fields share a Couchbase bucket: writes are
namespaced by origin (`<folder>@<hostname>`) so nobody clobbers anybody;
teammates' evidence surfaces through merged reads; independent agreement
promotes a claim to the team commons.

**Two ways to share, pick per team.** By default each seat's field is
local (`.membrane/field/`) and a connected bucket carries only what a seat
**publishes** (`entity_edit` `publish: true` — state, never code, under
`pub:<origin>:<eid>`) plus every seat's presence (the roster). That is the
light mode: teammates see the entities you chose to share, imported once
per boot. Everything else in this doc — merged reads, divergence, the
team commons — is the **merged field**, which every seat must opt into
with `MEMBRANE_FIELD_CLOUD=1`. A seat without the flag stays private
apart from what it publishes. The agent never routes
through a teammate's machine — only through the MEMORY. You are always
talking to your local model stack; the shared field is what makes the stack
wise.

## The day-one setup (each seat, ~5 minutes)

1. Point every seat at the same bucket and opt into the merged field
   (`membrane connect` writes the credentials to
   `~/.membrane/projects/<slug>.env`; add the flag there or in the shell):

       export MEMBRANE_FIELD_CLOUD=1
       export COUCHBASE_CONN_STRING=couchbase://<team-host>
       export COUCHBASE_USER=... COUCHBASE_PASSWORD=... COUCHBASE_BUCKET=membrane

   One bucket per TEAM, the `membrane` scope, the nine entity-field
   collections (created and indexed automatically on first boot).
   Projects distinguish themselves by the origin axis — do NOT add
   per-project buckets (that defeats independent-agreement promotion).
   `MEMBRANE_STORE=couchbase` alone no longer shares the field. The seat's
   existing local field is not uploaded: its origin starts empty in the
   bucket.

2. Run `python -m membrane.opencode` in the repo. Boot lines should say
   `[efield] capture on (CouchbaseBackend)` — `FileBackend` (or `CBLiteBackend`) means the flag
   is missing or the cluster was down at boot (the seat fell back to its
   local field for this run) — and `[seat] presence published (<origin>;
   N peer(s) on the roster)`. `presence planted locally` means the seat
   could not reach the cluster's `http://<host>:8091/pools`. The
   `gpeers: N other membrane(s) live` line counts membranes on THIS
   machine only (live peer asks go to `127.0.0.1`); teammates on other
   machines reach you through the bucket, never through live asks.

3. The embedder is per-seat resolved (first known embed model in Ollama).
   Keep the same embedding model on every seat (mxbai-embed-large is the
   default) — cross-model vectors do not share a geometry. Pin it:
   `--embed-model mxbai-embed-large` if the seats drift. (Moving a seat to
   another model later is `membrane reembed --efield --embed-model M`.)

## The work loop (what sharing actually looks like)

- **You code normally.** Every earned answer — a fixed flaky test, the
  deploy quirk, the reason the config gate exists — lands as events with
  your origin stamp. There is no "commit to team memory" step; living the
  work IS the write path.
- **Across time zones, without meetings:** dev in Tokyo earns "the windows
  runner needs `schtasks` quoting"; dev in Berlin hits the same wall 7
  hours later. B's block shows the entity both seats touched — A's evidence
  sentences and state next to B's own (merged reads, mass-weighted). B
  never pings A at 3am. (Live peer asks do not cross machines; the bucket
  is the path.)
- **Independent agreement = promotion.** Two origins making the same CLAIM
  (their own `claims` rows, content-keyed) is the evidence that a truth is
  commons material, not a seat's local habit. The night's consensus pass
  (`claims.promote_commons`) promotes it on its own, marked `consensus`
  (or `disputed` when a seat contradicted it); the operator's own path is
  `membrane: remember globally: X` / `membrane: promote #N`.
- **Disagreement is public, not data loss.** Two seats moving the same
  entity in opposing directions surface as a divergence conflict in both
  seats' blocks; a claim one seat contradicted goes `disputed` in the
  commons. Merge conflicts, but for knowledge, with the conflict shown
  instead of a rebase.

## The watch layer (know when the field moves)

`membrane: watch <topic>` tracks an entity (by its canonical id): the watch
report renders where-we-are lines whenever that entity is MENTIONED — yours
or a teammate's. This is the distributed team's ambient channel: plant
`watch release checklist`, go to sleep, wake to a digest of what the
overnight seats taught the field.

- Cap: 5 live watches (cap 7 at boot enforcement); planting over the cap
  evicts the lowest-charged one.
- Boundary alerts: a watch crossing its mention-count boundary raises one
  `ALERT` line (exactly-once); re-entry re-arms.
- `membrane: watches` = the board; `membrane: drop watch <#N|all|fragment>`
  removes.

## Operator rules, team edition

- **Write authority is shared, with provenance.** A `membrane: learn that
  X` in the AGENT's prose writes the field (a NOTE event, audited
  `origin: agent`; the operator path stays distinguished as `operator-note`).
  `--no-agent-writes` reverts the seat to propose-only for seats that want
  the strict human-hand covenant. Whatever the lane, model prose never
  writes a TEAMMATE's events — the origin axis still separates who wrote
  what (a rogue subagent on one seat cannot poison eleven silently — its
  events are forensically visible, and forgettable — by the seat that owns
  them — once trust weights land).
- **Agent learn hygiene:** the agent learns only EARNED lessons of its own
  work (the fixed quirk, the command that worked). No secrets, no user
  personal data, no transcript dumps — the length cap and the guards
  filter the bulk, and the operator's `forget #N` / `membrane review` paths
  remain the sanitation layer.
- **Forget sovereignty:** you can only tombstone YOUR origin's events. A
  teammate's junk is handled by (a) asking them, (b) demoting their
  origin's trust (the open item, below), or (c) earning the contradicted
  claim against theirs (a conflict both blocks show).
- **Trust weights (the one open item):** a seat with a weak routed model
  can move entities on junk. Per-origin trust scalars (multiplied into
  merged reads) are planned — until then, the honest mitigations are (b)
  and (c) above, and keeping operator ceilings per seat (`never pricier
  cloud than the picked model`).
- **Cross-project hygiene:** the origin axis keeps other projects' events
  out of your reads; the cross-project channels are the commons
  (`membrane: remember globally:`) and published entities, which is exactly
  the policy boundary you want: local by default, an explicit act to share
  beyond the repo.

## What to verify before going live (checklist)

Both seats run with `MEMBRANE_FIELD_CLOUD=1` and the boot line reads
`capture on (CouchbaseBackend)`. Without it, steps 1–3 cannot pass: each
seat sees only its own field plus what the other published.

1. Two-seat dry run: same project, two machines, two origins. Learn a
   memory on seat A (`membrane: learn that X`), confirm seat B's
   `membrane: query the field for <X>` surfaces it (A's evidence, tagged
   with the shared entity).
2. Disjoint writes: B's capture of the same project writes ITS origin only;
   `membrane doctor` on each seat shows `origin <tag>` and that seat's own
   entity counts; merged reads show both.
3. Independent agreement: both seats make the same claim; check the
   night promotes it (a `commons` claim listing both origins).
4. Kill switch: unsetting `MEMBRANE_FIELD_CLOUD` reverts the seat to its
   private local field (its rows already in the bucket stay there until
   removed). Unsetting the `COUCHBASE_*` connection (or the cluster being
   unreachable) also stops publish and presence. `--no-global` turns off
   the same-machine live peer asks; `MEMBRANE_EFIELD=off` stops capture
   altogether.
5. Audit trail: each seat's `audit.jsonl` carries the operator hand
   (origin agent/operator, watches, promotes); the per-origin keys make
   after-the-fact forensics possible without a central log.

## Why this shape (and not a shared chat log)

A shared chat log is a transcript: it grows linearly, everything is equally
salient, and reading it costs whoever reads it. A shared field is
COMPETENCE: entities carry only the mass of agreeing evidence, conflicts
make disagreement visible instead of burying it, and the block serves the
relevant slice at the moment of need — measured per-turn, not
per-onboarding-document. The time-zone win is not "async messaging"; it is
that Tokyo's lesson is Berlin's reflex, with no message in between.