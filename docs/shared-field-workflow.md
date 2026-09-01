# The shared-field workflow: a worldwide distributed team on one membrane

How a team spread across time zones codes against ONE shared memory. This is
the operator manual: what exists, what to type, what to expect. Companion to
`docs/multi-dev-field.md` (the substrate plumbing) -- this doc is the
PROCESS.

## The idea in one paragraph

Each seat runs membrane against its own coding agent (opencode + one field
per project). The fields share a Couchbase bucket: writes are namespaced by
origin (`<folder>@<hostname>`, field.py:137) so nobody clobbers anybody;
teammates' memories surface as marked context (veil poke at sim >= 0.90);
independent agreement promotes a note to the team commons. The agent never
routes through a teammate's machine -- only through the MEMORY. You are
always talking to your local model stack; the shared field is what makes
the stack wise.

## The day-one setup (each seat, ~5 minutes)

1. Point every seat at the same bucket:

       export MEMBRANE_STORE=couchbase
       export COUCHBASE_CONN_STRING=couchbase://<team-host>
       export COUCHBASE_USER=... COUCHBASE_PASSWORD=... COUCHBASE_BUCKET=membrane

   One bucket per TEAM, `membrane` scope, `exemplars` collection. Projects
   distinguish themselves by the origin axis -- do NOT add per-project
   buckets (that defeats independent-agreement promotion).

   TWO TIERS, two buckets: stable field data lives in the canon bucket
   (membase type, persists); unconfirmed candidates (fresh captures, the
   ephemeral tier) live in a dedicated EPHEMERAL-type bucket -- the
   `cortex-core` / `cortex-ephemeral` convention. Point at it explicitly
   with `COUCHBASE_EPHEMERAL_BUCKET`, or let boot resolve it:
   `<canon>-ephemeral` if it exists, else any `ephemeral`-type bucket, else
   one is created (256 MB, no replicas). No ephemeral bucket available?
   The tier falls back to a sibling `exemplars_ephemeral` collection in the
   canon bucket -- semantics identical, GC by doc TTL either way. The
   migration script (`scripts/cb_migrate_field.py`) routes flagged rows to
   the ephemeral bucket and canon rows to the canon bucket in one pass.

2. Run `python -m membrane.opencode` in the repo. Boot line should say
   `shared store: couchbase (...)` and `peers: N other membrane(s) live`.
   If peers: 0 while a teammate is online, their proxy is down or behind a
   firewall (peer asks are LAN/VPN-local; the bucket is the only WAN path).

3. The embedder is per-seat resolved (first known embed model in Ollama).
   Keep the same embedding model on every seat (mxbai-embed-large is the
   default) -- cross-model vectors do not share a geometry. Pin it:
   `--embed-model mxbai-embed-large` if the seats drift.

## The work loop (what sharing actually looks like)

- **You code normally.** Every earned answer -- a fixed flaky test, the
  deploy quirk, the reason the config gate exists -- is learned by your
  field with your origin stamp. There is no "commit to team memory" step;
  living the work IS the write path.
- **Across time zones, without meetings:** dev in Tokyo earns "the windows
  runner needs `schtasks` quoting"; dev in Berlin hits the same wall 7
  hours later. B's gate escalates, B's proxy pokes A's exemplar through
  the veil (sim >= 0.90) as marked context -- B's model sees it and either
  uses it immediately or re-derives and wins arbitration for the slot.
  Either way B never pings A at 3am.
- **Independent agreement = promotion.** Two origins landing on the same
  note (votes/uses, field.py:74-81) is the evidence that a memory is a
  commons candidate, not a seat's local habit. Scope suggestions surface
  these (`membrane: suggest` to review; the operator still ratifies with
  `membrane: promote`).
- **Disagreement is not data loss.** Two seats earn different answers to
  the same situation -> the weaker defers into `alternates` (cap 4) and
  can re-promote when reality votes for it. Merge conflicts, but for
  knowledge, with arbitration instead of a rebase.

## The watch layer (know when the field moves)

`membrane: watch <topic>` plants a spatial trigger (membrane/watch.py):
a probe + radius in embedding space. Every NEW memory landing inside --
YOURS or a teammate's that poked through -- queues a `[membrane-watch]`
report delivered on your next model turn. This is the distributed team's
ambient channel: plant `watch release checklist`, go to sleep, wake to a
digest of what the overnight seats taught the field.

- Cap: 5 live watches; planting a 6th evicts the lowest-charged one.
- Black-hole guard, two honest directions: a watch that swallows too many
  arrivals collapses (radius auto-shrinks) or goes dormant; a watch that
  earns no hits starves and sleeps. Attention is earned, not declared.
- `membrane: watches` = the board (charge, hit rate, dormant flags);
  `membrane: drop watch <#N|all|fragment>` removes.

## Operator rules, team edition

- **Write authority is shared, with provenance.** A `membrane: learn that X`
  in the AGENT's prose writes the field (tagged `agent-note`, audited
  `origin: agent`, same dedup/variety guards as any turn; the operator path
  stays distinguished as `operator-note`). `--no-agent-writes` reverts the
  seat to propose-only for seats that want the strict human-hand covenant.
  Whatever the lane, model prose never writes a TEAMMATE's cells, and the
  operator audit trail still separates who wrote what (a rogue subagent on
  one seat cannot poison eleven silently -- its notes are forensically
  visible, and demotable per-origin once trust weights land).
- **Agent learn hygiene:** the agent learns only EARNED lessons of its own
  work (the fixed quirk, the command that worked). No secrets, no user
  personal data, no transcript dumps -- the length cap and the guards filter
  the bulk, and the operator's `membrane: forget`/review paths remain the
  sanitation layer.
- **Forget sovereignty:** you can only delete YOUR origin's cells. A
  teammate's junk is handled by (a) asking them, (b) demoting their
  origin's trust (see below), or (c) out-earning it in arbitration.
- **Trust weights (the one open item):** a seat with a weak routed model
  can write junk that survives on volume. Per-origin trust scalars
  (`trust.json`, multiplied into arbitration ranking) are planned --
  until then, the honest mitigations are (b) and (c) above, and keeping
  operator ceilings per seat (`never pricier cloud than the picked model`)
  so no seat's model quality silently diverges.
- **Cross-project hygiene:** the origin axis keeps other projects'
  exemplars out of your recall; the ONLY cross-project channel is the
  global commons (`membrane: remember globally:`), which is exactly the
  policy boundary you want: local by default, explicit promotion to share
  beyond the repo.

## What to verify before going live (checklist)

1. Two-seat dry run: same project, two machines, two origins. Learn a
   memory on seat A (`membrane: learn that X`), confirm seat B's
   `membrane: query the field for <X>` surfaces it
   (tagged `owned by <A's origin>`).
2. Veil check: B recalls A's note only at sim >= 0.90 poke-through or
   escalation peer-ask; B's own weaker memories are not displaced by it.
3. Independent agreement: both seats earn the same note; check
   `membrane: suggest` fires a scope suggestion.
4. Kill switch: `--no-global`, `--no-router`, `MEMBRANE_STORE` unset are
   all independently sufficient to revert to a private field.
5. Audit trail: each seat's `audit.jsonl` carries the operator hand
   (`origin: agent|operator`, watches, promotes); the store's per-origin
   doc keys make after-the-fact forensics possible without a central log.

## Why this shape (and not a shared chat log)

A shared chat log is a transcript: it grows linearly, everything is
equally salient, and reading it costs whoever reads it. A shared field is
COMPETENCE: it forgets what failed to matter (compactor), ranks what the
world voted for (arbitration, votes/uses), and serves it at the moment of
need (recall, veil poke, peer ask) -- measured per-turn, not
per-onboarding-document. The time-zone win is not "async messaging"; it is
that Tokyo's lesson is Berlin's reflex, with no message in between.