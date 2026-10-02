# membrane

![Membrane](./centered_uroboros.gif)

> A **text-based hypervector entity-field hippocampus for opencode** that
> learns as you go, shares a Couchbase-backed team memory, and **orchestrates
> LLM calls** between local and cloud inference.

A **Python lab**. It runs as a local HTTP server on
`127.0.0.1` in front of Ollama (or Anthropic) — technically a proxy, but that
is the mechanism, not the point. The point is the field.

> **Why that name?** The project began as one question — *how do you measure
> "variety" for a memory that decides absorb-a-cached-answer vs
> escalate-to-the-model?* — and the answer is what makes the memory above
> possible and is why it is called **membrane**: a selective, *active* boundary
> between lived memory (the inside) and the incoming world (the outside) that
> absorbs what it has the variety for and escalates the rest. The full story
> lives in [The thermodynamic principle](docs/principle.md) and
> [The math findings](docs/math-findings.md).

This README is the **developer workflow** — install, launch, connect a team
field, and the daily loop. How the entity field works under the hood, how
sharing works, and the project's history are in the sub-documents (see
[Documentation map](#documentation-map)).

---

## Table of contents

- [Installation](#installation)
  - [Install the CLI](#install-the-cli-one-line-no-sudo)
  - [First run in a project](#first-run-in-a-project)
  - [Daily workflow](#daily-workflow)
  - [Other surfaces](#other-surfaces)
  - [Claude Code (Anthropic cloud)](#claude-code-anthropic-cloud)
- [Sharing a team field (Couchbase)](#sharing-a-team-field-couchbase)
  - [`membrane connect` (recommended)](#membrane-connect-recommended)
  - [Manual setup via env](#manual-setup-via-env)
  - [What sharing guarantees](#what-sharing-guarantees)
- [Checking on the field](#checking-on-the-field)
- [Documentation map](#documentation-map)
- [Development](#development)
  - [Run the tests](#run-the-tests)
  - [The variety toy](#the-variety-toy)
- [Status](#status)

---

## Installation

### Install the CLI (one line, no sudo)

```bash
curl -fsSL https://raw.githubusercontent.com/chedim/membrane/main/install.sh | bash
```

Installs the `membrane` CLI globally, user-local (`~/.local/bin`):
`uv tool install` if you have [uv](https://docs.astral.sh/uv/), otherwise a
managed venv at `~/.membrane/venv` + a symlink on your PATH. Re-running it is
safe (upgrade in place). To install from a local checkout instead (e.g. while
hacking on membrane):

```bash
MEMBRANE_REPO=/path/to/membrane bash install.sh
```

Requires Python ≥ 3.10 (numpy is the only import-time dep); embeddings/chat come from a running
[Ollama](https://ollama.com). Without installing anything, one-off via uvx:
`uvx --from "membrane @ git+ssh://git@github.com/chedim/membrane" membrane install`.

**Dependencies, honestly:**

- **Python ≥ 3.10** + **numpy** — the only import-time dep. `couchbase` (the
  shared field) and `paho-mqtt` (the event bus) are installed by default
  (`pip install -r requirements.txt` / the pyproject) but imported lazily;
  either missing degrades that feature, never the boot.
- **Ollama** (`brew install ollama` / <https://ollama.com>) — NOT auto-installed; embeddings
  and local chat need it running on `:11434`.
- **Couchbase** — where the field lives for real seats: one bucket per team,
  the `membrane` scope, nine collections (`events`, `sentences`, `terms`,
  `entity_states`, `entity_deltas`, `relations`, `claims`, `annotations`,
  `origin_stats`), created and indexed on first boot (`ensure_infra`). With no
  cluster configured or reachable the seat degrades to a RAM field — it runs,
  and nothing persists across restarts.
- **[opencode](https://opencode.ai)** — NOT auto-installed either; `membrane opencode`
  launches it if found on PATH (`sh <(curl ... opencode.ai/install.sh)`), otherwise the
  proxy runs bare and you point any OpenAI-compatible client at `http://127.0.0.1:11500/v1`
  (`--no-launch` skips the spawn entirely). The other surfaces (viewer, review,
  ingest, codegraph) need nothing beyond Python.
- The **viewer** ships inside the package (three.js + both pixel fonts are vendored,
  served same-origin at `/viewer`, opened in your browser on start; `--no-viewer --no-open`
  to disable) — no CDN, no node, no browser install.

### First run in a project

```bash
cd ~/projects/my-app
membrane opencode
```

That's the whole workflow. On first run in a directory it bootstraps that
project:

- auto-installs the eight agent skills (query / learn / forget / state /
  global-field / codegraph / ingest / restart)
  into `.opencode/skills/` there (project-scoped on purpose — an opencode
  launched elsewhere never sees them; skip with `MEMBRANE_NO_AUTOINSTALL=1`),
- opens an **entity field** for the project, namespaced by its origin
  (a sha1 tag of the project path) — events, entity states, relations,
  claims — all in the shared scope when one is configured,
- backfills what it can without cost: the codegraph (code events and
  `calls` relations) and, with `membrane backfill`, the project's history,
- if no memory sharing is configured, offers (once, interactive shell only)
  to connect a Couchbase cluster for this project — decline and it stays a
  private local field (see
  [Sharing a team field](#sharing-a-team-field-couchbase)).

It then starts the OpenAI-compatible proxy on `:11500` (backend Ollama at
`http://localhost:11434`, override `--backend`/`--model`) and opens opencode
pointed at it. Every turn is captured into the field (discourse and
observations as events), entities move as the conversation names them, and
the model is grounded by what those entities hold —
[docs/entity-field.md](docs/entity-field.md) is the design doc.

### Daily workflow

Inside opencode, the field answers five canonical frames itself —
**intercepts: no model call, never learned**:

    membrane: query the field for <topic>     read recall
    membrane: learn that <fact>               write a memory (a NOTE event)
    membrane: forget #N                       tombstone event #N and
                                              recompute what it touched
    field state                               memory health/stats
    membrane: suggest                         review scope suggestions for
                                              the cross-project commons

The agent may also emit these frames in its own prose; answers arrive next
turn as clearly-marked `[membrane-intercept]` blocks. Writes from model prose
execute by default (`--no-agent-writes` reverts to report-only): a learn runs
the same guards as any turn and is audited `origin: agent` (tagged
`agent-note`).

Everything else is ambient. Each turn the model sees what the field holds
about the entities it names (its evidence sentences, the latest observations,
the code, relations, conflicts) and — on later turns — only the **diff**:
which entity states moved since the last message it processed, from
conversation, code, other seats or entity scripts. Two safety lanes run on
every turn and stay advisory:

- **the gate** finds the earlier ask nearest this one and its answer; when it
  is not stale (no newer observation or conflict on the ask's entities), the
  pair is logged as an **A/B** — the replay next to what the model actually
  answered, judged for agreement. `--replay-ab P` serves the replay on a
  fraction P of eligible turns (default 0 = shadow: you are never bypassed);
- **the [membrane] monologue** in the thinking channel shows the gate verdict,
  field vitals, and the per-turn memory flow. `/monologue` toggles it.

Idle background loops run on a LOCAL cortex by default (`--idle-cortex-model`)
so a stuck background seat can never burn cloud tokens. Token economics and
routing: [docs/token-economics.md](docs/token-economics.md).

### Other surfaces

| Command | What it does |
|---|---|
| `membrane opencode` | the daily driver: seat proxy on :11500 + membrane's own serve surface (loopback) with the opencode TUI **attached as a thin client** — sessions, turns and tool parts live in membrane; opencode is display only (`MEMBRANE_TUI_DIRECT=1` restores the old direct-provider lane) |
| `membrane serve` | the same seat, headless: membrane's OWN opencode-compatible REST/SSE server on :4096 (opencode replaced, no child process) |
| `membrane backfill` | rebuild the field from history (code first, then the audit log; idempotent, resumable) |
| `membrane review` | print what the field learned (annotations, replay A/B with `--ab`) |
| `membrane connect` | (re)run shared-field onboarding for THIS project |
| `membrane reset` | kill the seat on :11500 (or `--listen`), relaunch `membrane opencode` fresh |
| `membrane install` | install `.opencode/skills/*` explicitly (+ `.gitignore` entry); prints the AGENTS.md blurb |
| `membrane auth` | store a cloud API key (anthropic, openrouter, gemini, groq, github, mistral) in `~/.membrane/auth.json` (600) — its models become routable rungs + picker entries |
| `membrane move` | migrate the field between stores (`--efield`: copy the entity-field collections between clusters) |
| `membrane migrate` | interactive migration front door: detects where the field lives, asks for the target, runs `move` |
| `membrane reembed` | move the field to another embedding model (`--efield --embed-model M`: re-embed every live event, re-apply) |
| `membrane update` | check the install source, show what changed, apply (`git pull --ff-only`) and re-exec |
| `membrane ingest` | cold start: absorb docs/README into the field (DOC events; a second ingest is a no-op) |
| `membrane codegraph` | stdlib-AST call graph (stats / impact / callers / callees) |
| `membrane doctor` | what's installed/running (CLI, skills, Ollama) + the field's vitals |
| `membrane tui` / `proxy` / `anthropic` / `demo` | RETIRED — print a pointer to `membrane opencode` |

`./run.sh` still works in-repo and delegates to the same CLI (bare = opencode;
`./run.sh restart` = `membrane reset`).

`MEMBRANE_EFIELD` selects the field's mode: `on` (the default: capture,
embed, apply), `shadow` (capture events only — a lab ablation), `off`.

### Serving headless (`membrane serve`)

`membrane serve` runs the seat with **no TUI and no opencode at all** —
membrane hosts its OWN opencode-compatible REST/SSE server
(`membrane/server.py`): sessions, messages, turns, tool parts, aborts, the
SSE event stream. Any opencode client connects unchanged: the [mobile
companion app](https://opencode.app/), IDE plugins, a script. The turn
loop calls the seat's OWN `/v1` on loopback — gate, injection, capture,
banner and the routing ladder run through the exact code path every other
client's turn uses, so the serve surface adds no separate memory, and the
model's membrane tool invocations (the MCP lane, built-ins all off) appear
to the client as ordinary tool parts riding `message.part.updated`
(pending → completed), so you can watch what it's doing from the room.

The connection triple is printed at boot:

    serve: http://<lan-ip>:4096  (native serve, basic auth user opencode)

- **Auth is basic auth** (opencode's env names, `OPENCODE_SERVER_USERNAME`
  / `OPENCODE_SERVER_PASSWORD`): no/incorrect credentials get `401`, only
  the pair in `.membrane/serve-auth.json` (mode 600, generated once,
  `--serve-pass` / `--serve-user` to override) gets through. Credentials
  survive restarts, so a device's saved connection keeps working.
- **Sessions and messages persist** beside the field
  (`serve-sessions.json` / `serve-msgs.json`, atomic write-through), so a
  device's history survives a seat restart. `--serve` + `--no-launch` is
  legal now — the surface is a thread, independent of the launch decision.
- **The TUI is a client too.** Every launch boot starts the serve surface
  (loopback-only unless `--serve`) and opens the opencode TUI **attached**
  to it (`opencode attach`, same basic-auth pair) — so a desktop session
  and a phone session are the same store: membrane's. The TUI's own turn
  loop, tool loop and local session storage are bypassed entirely; the
  attached client only displays what the server publishes. Old lanes:
  `MEMBRANE_TUI_DIRECT=1` (TUI fronting `/v1` directly) and
  `MEMBRANE_LEGACY_SERVE=1` (the `opencode serve` child).
- **Exposure is WiFi-only for now** (`--serve-host` defaults to `0.0.0.0`,
  the LAN): reach the server only from a network you own. The planned
  route makes the seat's phone (the boat's agDroid HA server) the host —
  the security model (VPN or LAN-only, no public exposure) gets designed
  there.
- `--serve-port` (default 4096, walks up if busy) and `--listen` (the
  proxy, still never leaves 127.0.0.1) are independent.
- The old lane survives as an ablation: `MEMBRANE_LEGACY_SERVE=1` restores
  the previous design (launch an `opencode serve` child). A failed serve
  bind prints loudly and the seat keeps running without the room-facing
  surface — never a dead seat over a surface.

### Claude Code (Anthropic cloud) — RETIRED

The old `./run.sh anthropic` / `--anthropic` observer mode (a proxy on
`:11435` in front of Claude Code) was cut with the proxy — `membrane
anthropic` now only prints the retirement pointer. The opencode seat is the
surface; to route Anthropic models through it, `membrane auth` stores the
key (`~/.membrane/auth.json`, mode 600) and its models become rungs on the
router's ladder.

---

## Sharing a team field (Couchbase)

By default a seat's field is private (RAM, or a local cluster). To make a team
share one substrate — same project, different machines, one memory — point
every seat at the same Couchbase bucket: one bucket per **team**, the
`membrane` scope, the nine entity-field collections; projects distinguish
themselves by the **origin** axis (a seat writes ONLY rows keyed by its own
origin — do NOT add per-project buckets, that would defeat cross-seat
agreement).

### `membrane connect` (recommended)

Run in the project directory; answer four prompts:

```bash
membrane connect
#   conn string   [couchbase://127.0.0.1]
#   user          [Administrator]
#   password      (hidden)
#   bucket        [membrane]
```

The same offer appears automatically on the first `membrane opencode` /
`membrane reset` in a project that has no store configured (interactive shell
only; declining is remembered — it becomes a private field, and `membrane
connect` re-opens the question).

**Secrets never touch the repo.** Credentials are stored per-project OUTSIDE
it, at `~/.membrane/projects/<slug>.env` (mode 600, slug = project path +
hash), and are exported into the environment at every launch from that
directory. A `.env` inside the repo would leak on the first `git add .` —
that failure mode is designed out.

Kill switch: `MEMBRANE_NO_ASK=1` silences the launch-time prompt (an explicit
`membrane connect` still works, as does plain env config).

### Manual setup via env

The classic path still works (cortex-experiment's env convention):

    export MEMBRANE_STORE=couchbase
    export COUCHBASE_CONN_STRING=couchbase://<team-host>
    export COUCHBASE_USER=... COUCHBASE_PASSWORD=... COUCHBASE_BUCKET=membrane

A **local** cluster answering at the default endpoint is auto-detected at
boot — no config needed on a dev box that already runs one. Unreachable
cluster → transparent fallback to the RAM field (degradation, never failure).

### What sharing guarantees

Mechanics that make sharing collision-free (details in
[docs/multi-dev-field.md](docs/multi-dev-field.md) and
[docs/entity-field.md](docs/entity-field.md)):

- every event, state, relation and claim is **namespaced by origin** (the
  seat-id where it was born); writes only ever touch own keys; foreign
  rows are read-only inputs to **merged reads** — a foreign entity's
  effective state is the seed plus the mass-weighted mean of every seat's
  displacement;
- two seats that moved the same entity in opposing directions produce a
  **divergence** — surfaced as a cross-seat conflict in both blocks, never
  silently overwritten (nothing is destroyed);
- a seat's working set includes foreign states near what the session is
  talking about (ANN) plus the **commons**: claims the same way from two or
  more seats (promoted at night; `disputed` when a seat contradicted it);
- teammates online get **live peer asks** (bounded fan-out, LAN/VPN);
  `peers: N live` at boot.

The full team process — day-one setup checklist, the async work loop across
time zones, the watch layer, operator rules — is
**[docs/shared-field-workflow.md](docs/shared-field-workflow.md)**.

---

## Checking on the field

- `field state` (in opencode) / `membrane state` / `membrane doctor` — the
  field's vitals: mode and backend, live events by stream, entities by tier
  (nursery/live/dormant), geometry (effective rank, free energy, the conflict
  fraction), replay A/B ledger, the last night pass.
- `membrane review` (`--ab` for the replay pairs) — what the field learned.
- The MCP `state` tool exposes the same payload to the agent.

What the numbers mean and how the field's "thermodynamics" work:
[docs/principle.md](docs/principle.md) (plain words) and
[docs/math-findings.md](docs/math-findings.md) (the measured math), with
[docs/entity-field.md](docs/entity-field.md) connecting both to the code.

## Lifecycle: updates, restarts, compatibility

- **Update check at boot.** On `membrane opencode` / `membrane reset`, membrane
  checks its install source (the git checkout it runs from, or the pip/uv
  direct URL it was installed from). If an update exists it prints the
  CHANGELOG (the commits between you and the remote tip) and asks
  `Update membrane now? [y/N]` in the terminal — answering yes pulls
  (`--ff-only`, a dirty tree is refused) and re-execs with the same argv.
  Non-interactive sessions are never interrupted; `membrane update` runs
  the same path manually. Opt out with `MEMBRANE_NO_UPDATE_CHECK=1`.
- **Restart only from the shell.** `./run.sh restart` (= `membrane
  reset`: kills the seat listening on :11500 — or `--listen` — and
  relaunches `membrane opencode`) is the safe procedure — see the
  membrane-restart skill. Membrane
  never restarts itself while running (no file watcher, no self-re-exec);
  a restart loses nothing: the field re-hydrates from its backend
  (`membrane reembed --efield` is the one way a restart CAN change results
  — moving to another embedding model).
- **Embedding-model moves.** The field's vectors are pinned to a model
  (`terms` seeds are cached per model); `membrane reembed --efield
  --embed-model M` re-embeds every live event and re-applies the log in
  order — the rebuilt field pins equal to a fresh one.

---

## Documentation map

| Doc | What's in it |
|---|---|
| [docs/entity-field.md](docs/entity-field.md) | **THE FIELD.** Design: events → entities → readers; the update rule and statistics; relations and claims; injection (full block / state diff); the gate + replay A/B; storage, sharing, the background mind; the e17–e25 evidence |
| [docs/shared-field-workflow.md](docs/shared-field-workflow.md) | **Team field: the process.** Day-one setup, the async loop, watches, operator rules, go-live checklist |
| [docs/multi-dev-field.md](docs/multi-dev-field.md) | **Team field: the substrate.** The origin axis, disjoint writers / merged reads, divergence conflicts, the commons — what makes multi-seat sharing collision-free |
| [docs/global-field.md](docs/global-field.md) | The global commons: facts true beyond one repo; operator promotion, consensus, residency |
| [docs/background-mind.md](docs/background-mind.md) | **The background mind:** thinker + dreamer on events, the NightClock, births/junk/activity on entity stats, entity scripts and tracks, kill switches |
| [docs/vsa-token-lane.md](docs/vsa-token-lane.md) | **Entity keys + relations (the VSA lane).** `eids.py` canonical ids, `entity_key`/`role_key` binding, the relations composites, structure-awareness numbers, consumers, kill switches |
| [docs/token-economics.md](docs/token-economics.md) | Where the savings come from (intercepts, the replay lane, the state diff) + the router: the ladder, the ceiling, the outage fallback, the free-tier default budget |
| [docs/monologue.md](docs/monologue.md) | The `[membrane]` thinking-channel banner: every line explained, the memory flow (gave/learned), `/monologue` toggle |
| [docs/symbol-birth.md](docs/symbol-birth.md) | The symbol crystallization: how the hierarchy is born — symbols come into being through language; the structural membrane mapping, the consequence to keep |
| [docs/principle.md](docs/principle.md) | "Only variety absorbs variety" — Ashby's law with the telos flipped; effective rank and free energy measured on entity states; the conflict tax |
| [docs/math-findings.md](docs/math-findings.md) | The measured math: the substrate-phase experiments (cosine primary, novelty specialist) and e18–e25 (spans, streams, coherence, relations, claims) |
| [docs/ab-critic-scorecard.md](docs/ab-critic-scorecard.md) | The A/B scorecard vs a plain cosine cache — six criticisms faced honestly |
| [docs/fork-port-notes.md](docs/fork-port-notes.md) | Fork notes (the fork now runs its own memory design; what it took to port membrane's ideas there) |
| *the cycled / endless environment* (sibling `cortex-experiment` repo) | The theory notes behind the principle (the loop, and the trap unbounded variety sets) |

Retired with the exemplar field (kept in git history, removed from `docs/`):
`close-the-gap.md` (TencentDB-gap work, superseded by the code-evidence
lane), `post-restart-plan.md` (the pre-rewrite roadmap), `exploration-game.md`
and `dev-organism.md` (the toy world and the cell organism — cut in
`070af25`).

---

## Development

Source layout: `membrane/` (the field: `streams` → `efield`/`efstore` →
`entity_block`/`efgate`/`night`, the seat proxy, the native serve server
(`server.py`, opencode-compatible), the opencode host (the legacy ablation),
the CLI), `experiments/` (the e-series rigs behind math-findings), `tests/`
(unittest + pytest, plain-python runnable).

### Run the tests

```bash
python -m unittest discover -s tests     # or: .venv/bin/python -m unittest discover -s tests
python -m pytest tests/test_global_field.py tests/test_lab_isolation.py   # the field-backed ones
```

No server needed: everything runs against in-process fakes
(`tests/_ef_fakes.py` — a sha256-seeded embedder and a lazy fake cluster that
runs exactly the SQL++ the field emits).

### The variety toy

The original variety experiment (what makes a store confident vs unsure —
the gate's calibration in one command):

```bash
pip install -r requirements.txt   # numpy only
python experiments/variety_toy.py
```

Expected: the low-variety store has low effective-rank and low spread; a query
off its manifold reads as highly novel (gate escalates), while the same query
against the high-variety store reads as in-manifold (gate acts).

---

## Status

Working lab, in daily use on its own development (dogfooded: this repo's own
field learned the codebase while it was being written — the backfilled field
answers `membrane state` with ~14k live entities, and its background mind
digests, dreams, and speaks about its own work). Version **0.2.0b1** — the first beta-testing release; API and flag surfaces may still move. **Private project** — no
license yet; all rights reserved by the author.