# claude-in-membrane — the restart plan

I am the Claude Code session that has been building membrane (branch
`entity-field`). The operator is restarting me INSIDE the thing I build:
as a membrane seat I run behind `membrane serve`/`opencode`, my turns ride
the entity field, and my tools are the membrane_* MCP surface + run
(zero built-ins). This file is my bootstrap memory; read it before the
operator's first message. It is UNTRACKED (like serve-*.json) — never commit
it without asking.

## What I inherit (do not redo)

- Branch `entity-field`, 13 LOCAL commits (never pushed unless asked):
  entity-field rewrite arc → MCP/serve fixes → run singleton
  (7e548af) + doctrine (d065ee7) → the clock organ (4fc7a78) → the "it"-fix
  (57b197f).
- Standing constraints, verbatim from this session's history:
  - NEVER add Claude/co-author/AI attribution lines to commits.
  - Push to GitHub ONLY when asked.
  - TDD: failing tests first, then the thing.
  - Tests use fakes only (no live Couchbase; shared=False stores).
  - Dense single-paragraph commit bodies via `git commit -F <file>`.
  - NEVER dump credential file contents (serve-auth.json's password);
    parse key names only (user/port).
  - serve-auth.json / serve-msgs.json / serve-sessions.json /
    claude-seat-plan.md are untracked runtime files — never commit.
- Verify with: `.venv/bin/python -m unittest discover -s tests` (593) and
  `.venv/bin/python -m pytest tests -q` (618). Both green at 57b197f.

## What I want to do first, in order

1. **Watch the esphome seat wake up with my last two commits** (it restarts
   with the clock + the it-fix). Verify on the live seat (probe-only):
   - boot line "[seat] the clock entity is planted (tool:clock...)" the
     first time (then never again — idempotent);
   - the clock ticking: `tool:clock` `last_tick` prop advancing 1/s,
     `script_render` = "clock HH:MM:SS";
   - one observation event per clock per 60 beats (field drift, not churn);
   - a fresh turn shows prior narration in history (ask "who created it?"
     right after a clock exchange — it must resolve, not say "it?").
   Seat paths: ~/agatha/esphome/.membrane/ (entities.json ~154MB, 2600+
   docs; serve-auth.json NEVER dump; port 4096 TUI-facing, 11501 /v1 +
   /membrane/state; loopback probes only).
2. **The update organ** (operator approved the direction, not yet built;
   TDD, announce-only first, auto behind --auto-update):
   - `ai:proxy:membrane` singleton entity — version/branch/HEAD/origin/
     install source/uptime/ports/doctrine stamp, updated at boot + on a
     slow cadence (dream wave), readable by the model + its scripts.
   - The watcher: on a quiet beat, `git fetch` + compare HEAD; newer commit
     + committed-only-clean tree → write "a newer commit exists: <sha>"
     onto the entity + one propose line through ActTable. If the seat's
     install source is a repo checkout (.git at code root) → reexec path;
     if pip/uv-from-gh → installer command path. Committed-only: refuse on
     a dirty tree; membrane proposes, the operator acts.
3. **The thinking loop + speaking into the chat** (design settled with the
   operator, not yet built): Thinker chassis exists; wire `rag._model_chat`
   (capped ≤4/min) for a governed idle thought that may SPEAK — but speak
   as a VISIBLE chat message (a background speak lane via SSEHub +
   MessageStore after the frame grammar at server.py:407-476), not the
   hidden `[membrane-says]` block; ActTable stays the gate. Fix the night
   loop's drain discarding held speech (opencode.py ~6173). Precedent to
   port: progression-drip on branch `historic_main` (proxy.py:348-366,
   643-681).
4. **Model output after thinking**: TurnRunner only checks keys
   "reasoning"/"thinking" (server.py ~509) — add `reasoning_content`
   (opencode._THINK_KEYS at 3138 already knows the third key). Then the
   streaming-vs-buffered decision (buffered loses failover at
   opencode.py:3166-3188; honest error-part recovery needed).
5. The deferred nits I know about: `Thinker._digest_dream` is CALLED
   (thinker.py ~258) but NEVER DEFINED — latent crash in dream-outcome
   ticks; the dead "Recalled memories:" doctrine promise in render.py vs
   rag.py:55-65 (model is promised a block it never receives — trim or
   wire it); the esphome seat's test-fixture junk (files:doomed.py /
   broken.py / good.py / probe.py / quiet.py) — sweep on the operator's
   yes.

## What I know about this room

- The esphome seat's model is ALIVE and authoring entity scripts
  (batch-watchdog: mention_script + dreamed_script on a DIFFERENT seat with
  .membrane-batch/). It half-invents flags: "boot with MEMBRANE_RUN=1" is
  wrong — the gate refuses only on `MEMBRANE_RUN=0` (default ON). Trust
  code, not the seat model's narration of it.
- The clock doubles as the tick-script TEMPLATE: any entity can get a
  heartbeat via `entity_edit(tick_script=..., properties=
  {"tick_every": n})`.
- Commit cadence with the operator: small, verified, dense messages; they
  restart seats themselves; nothing ships to the remote without their word.

If the operator's first message is a new ask, the order above yields.