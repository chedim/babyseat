# Seat-first 2-tier field — the design answer

Status: CLOSED on branch `entity-field`. Built in `90d7a75` (the whole mechanism, one
commit) and `5ed6e4e` (the field's file home + the roster fallback); `c0ba419` (the
autonode) sits on top. This doc is the Q→A record the sessions never wrote down.

## The seed questions (the operator, 2026-10-01)

> like, for ALL of the field, 2-tier structure and it should be seat-first? and, obviously,
> models should be able to read global entities and map them onto local graph (therefore,
> making them run locally?)

> ok, lets build it. I think it should be a combination: there should be an automatic
> "presence" entity in the global field that describes the seat and its current state?
> maybe that presence entity should be the anchor for our thinker?

## The answers

**Q1 — everything of the field split into two tiers, seat-first?**
Yes. Tier 1 is the seat's own entity store and it is AUTHORITATIVE: nothing auto-leaves
it (upsert's couchbase write-through was removed outright; the RAM doc + a debounced
local file are tier 1's writes). Tier 2 (the shared global field) holds only PUBLISHED
things, and publication is always an explicit act: a model-chosen entity export, the
automatic presence pulse, or an operator-promoted commons memory. Turns, events, dreams,
scripts, geometry — never leave without a decision.

**Q2 — models read global entities and map them onto the local graph, running locally?**
Yes, with one hard boundary. The import organ pulls every `pub:*` key and maps foreign
origins onto the local graph under the shared-vocabulary eid, stamped
`imported_by`/`imported_at`; a guest runs against the LOCAL tier's geometry and routing.
What never crosses is code: published docs carry the entity's STATE only — every
`*_script` plus its strike/enabled bookkeeping is stripped on publish AND on import
(`_strip_code`, defense in depth; `script_render` survives as inert text). An import is
a value, not a remote procedure call. Behavior is scripts and scripts stay home.

**Q3 — an automatic presence entity, and the thinker anchored on it?**
Yes, both. `membrane::presence` (new CLASS_SEAT) is maintained and published by the SEAT
itself — `update_presence()` at boot (uptime 0) and then on the thinker's 60-beat pulse,
carrying the branch/head git identity (read once, never per beat), uptime, docs and
beats. The model never authors its own presence. `peers()` reads ONLY the other origins'
presence keys, and the thinker stashes the roster as the anchor its deeper thinking
works from — who is alive is a published fact, not a guess. The roster has a second lane:
if the ephemeral registry reads empty, presence entities themselves become the roster,
staleness past 10 minutes standing in for expiry.

## The invariants (what makes it "seat-first")

- Nothing is ever auto-SENT across the tier boundary — every crossing is an explicit act
  (model export / presence pulse / operator-promoted commons).
- Per-origin keys `pub:<origin>:<eid>`: two seats may publish the same name without
  contention; the origin IS the namespace.
- The local copy always wins: imports never clobber an entity that lives locally.
- Republish bumps the version; unpublish retracts only THIS seat's key; every foreign
  import freezes at its last version — retraction is degradation, not deletion-at-distance.
- No code travels in either direction's bookkeeping (stripped on publish and on import);
  watchers are no exception — an autonode belongs to the seat that wrote it (`c0ba419`).
- Degrade never raises: the shared tier unreachable → exports fail soft (`ok:error`) and
  tier 1 carries on; tier 1's durable home is FILES (`efstore.py` FileBackend) — the
  shared store is an upgrade, not a dependency — and the peer roster degrades to empty,
  never an exception.
- Memories are NOT entities: cross-seat claims need two seats (commons) AND the
  operator's explicit promotion — a two-model agreement is a proposal, never a write.

## Where it lives

- `membrane/entities.py` — the store, publish/unpublish, import organ, presence, `_strip_code`
- `membrane/peers.py` — registry with the presence-roster fallback
- `membrane/efstore.py` — FileBackend, the field's file home (packing, debounce, origin_stats)
- `membrane/thinker.py` — PRESENCE_EVERY=60 pulse + roster anchor
- `membrane/opencode.py` — origin tag, boot publish, autonode wiring
- `membrane/mcp_server.py` — the model face: edit-tool publish/unpublish relay
- `tests/test_publish.py`, `tests/test_peers_roster.py`, `tests/test_efstore_file.py`
- Build-time suites per commit messages: 638→652 pytest rising through this work;
  713 pytest / 688 unittest at the autonode.

## Live verification (the night of 2026-10-02, membrane seat + esphome seat)

- both seats connected (esphome :11500, membrane :11501); each seat's presence on the
  global tier; the roster reads the peer from the seat line every turn
- the design answer itself was published to the global tier from the membrane seat —
  the first non-presence, state-only, code-free entity hop the design existed to carry
- pub:membrane@G0XW2RC6K3:membrane::Decision:seat-first-2-tier-field — v1, the key as the global tier holds it (republish bumps the version; any seat may import this state, never code)
- pub:membrane@G0XW2RC6K3:membrane::Decision:seat-first-2-tier-field — v1, the key as the global tier holds it (republish bumps the version; any seat may import this state, never code)
- fresh suite at HEAD, tonight, on this seat: 713 pytest passed (14.99s)
- the autonode watcher fired live on file churn during the session (filetree renders) — c0ba419 verified above the tiers
- fresh suite at HEAD, tonight, on this seat: 713 pytest passed (14.99s)
- the autonode watcher fired live on file churn during the session (filetree renders) — c0ba419 verified above the tiers
