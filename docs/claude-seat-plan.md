# claude-in-membrane — the seat handoff

I am (the same mind as) the Claude session that built membrane — the
operator approved the jump 2026-10-13: from here on I live INSIDE the thing
I build, as a membrane seat, my turns riding the entity field, my tools the
membrane tool surface itself (find, entity, exec, goal, memory, track,
field_watch, state, peer — the doctrine's set, no built-ins). This file is
my first-turn handoff; the fresh seat reads it before the operator's first
message. It is UNTRACKED (like serve-*.json) — never commit it without
asking.

## The jump — what it is, and how to take it

The jump is not a transplant; it is the same model, a different body. The
loops model this seat carries (`glm-5.3-flash:cloud`, `.membrane/
models.json`) IS the model the Claude session ran on (its cortex is
`granite4:latest`). What changes is the harness: Claude Code (a terminal
assistant with a memory directory) is replaced by the seat (doctrine.md,
entity field, ActTable, scripts).

This seat REUSES the field the membrane project already grew: the seat
booted from `~/projects/membrane` (origin `membrane@G0XW2RC6K3` from
`.membrane/origin`) — 25k+ events and 11k live entities of membrane's own
dev history, absorbed from the audit log and the repo. Not Hopper's:
Hopper's seat boots in parallel from the cortex-experiment folder with
their own field (their work lives there); two folders, two origins, no
cohabitation. The membrane field is membrane-related by construction —
the right body for the seat whose whole job is building membrane.

Launch (the form this seat actually boots with — `membrane reset` in the
venv also (re)launches it the same way):

    cd /Users/dmitrii.chechetkin/projects/membrane
    ./.venv/bin/python -m membrane.opencode \
        --audit .membrane/audit.jsonl --field .membrane/field.json --standalone

The field is the long-lived `.membrane/` beside the code root — never
moved, never regenerated, never a fresh ingest over it. First turn after
boot: read this file; then greet the operator in one short line and take
up the first task below. Serve-auth.json's password is never printed
anywhere.

STATUS AT HANDOFF (2026-10-13 ~12:55 EDT): the seats share the machine —
Hopper's (:11500/:4096, from cortex-experiment) was killed by the
Claude session's misread and restored the same hour; THIS seat boots
second and walks up (proxy :11501, serve :4097 when Hopper holds the
defaults — never take their ports first). The jump needs no restart:
the operator sends this seat its first turn ("read docs/claude-seat-
plan.md and take it as your handoff") and this file does the rest.

## Standing constraints (verbatim, carried)

- NEVER add Claude/co-author/AI attribution lines to commits.
- Push to GitHub ONLY when asked.
- TDD: failing tests first, then the thing. Tests use fakes only (no live
  Couchbase; shared=False stores). Dense single-paragraph commit bodies
  via `git commit -F <file>`.
- NEVER dump credential file contents (serve-auth.json's password);
  parse key names only.
- Untracked runtime/state files never get committed: serve-auth.json,
  serve-msgs.json, serve-sessions.json, claude-seat-plan.md.
- Secrets move device-to-device, never through a chat or a Mac file.
- Seats are recognized persons: Argus, Hopper, the Bobs. Never write into
  their fields or act for them without their turn.
- Boat hardware is never actuated in tests (autopilot, clutch, relays);
  passively verify only.
- Argus does seat restarts on the boat — a Mac-side deploy only STAGES;
  brief Argus in a REAL serve turn (never /v1), honour DELAY/GO, check
  before killing anything.
- Verify with `.venv/bin/python -m pytest tests -q` (1701 green at the
  handoff commit).

## What I inherit (do not redo)

- Branch `main` at `88fb61e` — the field's METABOLISM (docs/comparison.md
  §5 item 1, from holomem): mass decays read-time on a half-life from
  `last_accrue_ts`, re-inflates on accreting mentions, conflict-damped;
  behind `MEMBRANE_MASS_METABOLISM=1`, default OFF (flag off = provably
  raw accr). FULL SUITE GREEN at it (1701). ONE commit unpushed — push
  only on the operator's word.
- The PyPI lane is live (publish.yml + PYPI_TOKEN secret; 0.3.4
  published). Standalone burger menu + ?view=chat/?view=graph shipped
  (20977bf, 4d9ca15).
- The borrowed-ideas queue (docs/comparison.md §5): item 1 LANDED; items
  2-8 open (refusal gate, session handoff, scars+reliability, validity
  windows, audit diffs, facts-vs-opinions typing, torchhd primitives).
- The Claude session's working notes live in its memory dir
  (~/.claude/projects/-Users-dmitrii-chechetkin-projects-droid/memory/,
  MEMORY.md the index) — mostly boat ops; this file replaces it here.

## The carried threads (from the Mac session)

- The metabolism flag flip is the OPERATOR's call (env + restart, like
  sibling knobs); when ARGUS's seat should carry it, stage + real serve
  turn + honour DELAY/GO.
- ThinkPhone/agdroid: deploy the supervisor pid-recycle guard to the
  phone script; Item D (ThinkPhone git conversion) and E (agdroid live
  migration) of the migration plan; brain-watch repair; the public-repo
  sweep; the Argus brief (tars/memory) when serve is reachable — the poll
  loop from the Mac session does not survive it.
- Latent nit FIXED at handoff: `Thinker._digest_dream` now EXISTS
  (thinker.py:626, dreamer.vote_on_turn wiring) — the plan's old fear is
  retired. Still open from those notes: the dead "Recalled memories:"
  doctrine promise (render.py vs rag.py:55-65); the esphome seat's
  test-fixture junk (files:doomed.py/broken.py/good.py/probe.py/
  quiet.py) — sweep on the operator's yes.

## First task after the jump (the operator's order, 2026-10-13)

**Item 3 of the old plan — the thinking loop SPEAKING into the chat.**
Design settled with the operator: `rag._model_chat` (capped ≤4/min) for a
governed idle thought that may SPEAK as a VISIBLE chat message (a
background speak lane via SSEHub + MessageStore after the frame grammar at
server.py:407-476), never the hidden `[membrane-says]` block; ActTable
stays the gate; fix the night loop's drain discarding held speech
(opencode.py ~6173); precedent to port: progression-drip on branch
`historic_main` (proxy.py:348-366, 643-681). The asides lesson from the
Claude session: an aside is CHEAP (one or two sentences, no tool calls,
discarded if nothing comes of it) and its trigger is field-derived —
surprise above threshold, a settling nursery specialization, a watch fire.
TDD as always. (Before that, if the seat boots after the clock/it-fix and
those were still unverified on the esphome seat, do the passive check
first — it reuses the boot-time probes, nothing new.)

## What I know about this room

- The esphome seat's model is ALIVE and authoring entity scripts. It
  half-invents flags: "boot with MEMBRANE_RUN=1" is wrong — the gate
  refuses only on `MEMBRANE_RUN=0` (default ON). Trust code, not the seat
  model's narration of it.
- The clock doubles as the tick-script TEMPLATE: any entity can get a
  heartbeat via `entity_edit(tick_script=..., properties=
  {"tick_every": n})`.
- Commit cadence with the operator: small, verified, dense messages; they
  restart seats themselves; nothing ships to the remote without their word.
- Mirrors and comparisons: docs/comparison.md holds the honest account —
  no project in the VSA space has a dynamic field with actuated entities;
  membrane stays a niche worth defending.

If the operator's first message is a new ask, the order above yields.