[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23550667.svg)](https://doi.org/10.5281/zenodo.23550667)
![babyseat](docs/logo/logo-1280x640.png)

**Talk to it. It remembers.**

babyseat is an AI seat with a memory that learns in real time. Everything you
and your AI say passes through it, and it learns from every message as it
happens. Inside lives Blurp, a small self that grows up on that memory, one
tested skill at a time: Alan Turing's "child machine" idea, taken literally.

- **Works today:** real-time learning, a body, aiming at its own targets.
- **Still learning:** recognizing itself (its earlier pass was voided and is
  being rebuilt), inventing new things, writing its own replies, running on an
  embedding model alone.

![Membrane](screenshot.png)

![Membrane](docs/comparison.png)

It runs locally in front of [Ollama](https://ollama.com), on its own or inside
[Home Assistant](docs/home-assistant.md).

> **The names.** **babyseat** is the whole project: the seat, its web UI,
> Blurp, the Home Assistant integration and the Docker image
> (`pip install babyseat`, `chedim/babyseat`). **membrane** is its memory and
> proxy, so the `membrane` command, Python module, `MEMBRANE_*` variables and
> `.membrane` folders keep that name. Why "membrane": a living boundary that
> absorbs what it already understands and passes the rest on
> ([the maths](docs/math-findings.md)).

---

## What makes it different

Most memory projects give your agent a database to search. membrane is a
different kind of thing.

### 1. A real-time proxy that learns from whatever passes through it

membrane sits in the path of your text and parses it into experience as it
goes by: a meaning slurper. Each message's people, files, words and commands
become entities, things said together drift together, and what is new gets
measured as surprise. The learning needs only an embedding model, with no LLM
step to summarize, extract or tag anything. Today it sits in front of AI models
(OpenAI and Ollama APIs), but the same capture takes any text stream.

### 2. Surprise does the learning

The field runs one small loop: take in what happened, compare it with what it
expected, and let the difference adjust memory and the next prediction. Each
new message is split into what the field already knows plus the news
(a non-negative least-squares fit), so surprise is a number, not a guess.
There is no central program deciding what to remember.

### 3. Memory has a shape

Not a pile of text chunks: an entity field with a geometry you can read and
measure. Entities have positions, mass, relations and life stages
(nursery → live → dormant). `membrane state` prints the shape of a running
field, and [docs/math-findings.md](docs/math-findings.md) holds the experiments.

### 4. It is the seat, not a plugin

The memory is the running process: its own chat UI, voice, API and Home
Assistant agent. Conversation and memory are one store. While you are away, a
background mind keeps working: a thinker digests, a dreamer consolidates at
night, watchers react to files. This repo's own field holds about 14,000
entities; it learned the codebase while the codebase was being written.

### 5. Nothing to curate

No schema, no tags, no hand-built graph. Entities are born when your work names
them, and claims and relations pile up on their own
([symbol birth](docs/symbol-birth.md)).

### 6. Every write is checked

Whatever the model writes to memory passes the same guards as a chat turn
(dedup, variety, length, junk), and every write is audited with where it came
from. A replay lane checks past decisions against what the model actually
said ([the scorecard](docs/ab-critic-scorecard.md)).

### 7. Seats share without overwriting each other

Each seat keeps its own field on its own disk and publishes only the entities
it chooses. When seats disagree, the disagreement is shown, never silently
overwritten. Teams can opt into one merged field
([sharing with a team](#sharing-with-a-team-couchbase)).

### 8. Local first

No account, no cloud, no telemetry. Chat and embeddings run on your Ollama, the
field lives on your disk, and a shared bucket is optional: with it down, the
seat still boots. The license is PolyForm Noncommercial: free for personal, research and
other noncommercial use ([LICENSE](LICENSE)).

### How it maps to the patterns you've already seen

| The usual approach | What membrane does instead |
|---|---|
| Paste documents into a vector store, query chunks | Records events as they happen; recall returns past situations and what each named thing holds |
| Summarize or replay the transcript | Never replays the transcript: each turn gets only what changed since your last message |
| Hand-curate a knowledge graph | Structure grows from experience, and every piece traces back to the events behind it |
| Memory inside your agent framework | The seat is the runtime: sessions and memory live in one store and can't drift apart |
| A hosted memory API | Your disk, or your own bucket; it works offline |

---

## Quick start

babyseat runs three ways: standalone, in Docker, or inside Home Assistant. All
need an [Ollama](https://ollama.com) server with one chat model and one
embedding model (`ollama pull qwen3:8b && ollama pull mxbai-embed-large`, or
any cloud model Ollama serves), and 1–3 GB of RAM for the seat.

### Standalone: its own web UI

```bash
pip install babyseat               # or: uv tool install babyseat
mkdir ~/bob && cd ~/bob
babyseat                           # = membrane standalone; opens http://localhost:4096/
```

You get membrane home:
- **the field in 3D,** drawn as a ball
  ([how to read it](docs/standalone.md#reading-the-field-the-sphere));
- **the chat;**
- **voice:** "Hey Bob". A new seat is Bob until you agree on a name together,
  and it will ask.

Other devices on your network open `http://<this machine>:4096/`. They can
read freely, and they ask once for the seat token before writing
(`membrane token seat` prints it). Callers from outside your network are
refused, unless you turn on [JWT mode](docs/auth.md). The microphone needs
`localhost` (a secure origin), and the first voice use downloads about 65 MB
of speech models.

### Docker

```bash
docker run -d --name membrane \
  -p 4096:4096 -p 11500:11500 \
  -v membrane-data:/data \
  -e MEMBRANE_BACKEND=http://host.docker.internal:11434 \
  -e MEMBRANE_EMBED_MODEL=mxbai-embed-large \
  --stop-timeout 60 \
  chedim/babyseat:latest           # then open http://localhost:4096/
```

The same standalone seat in a container: the web UI on :4096 and the
OpenAI-compatible proxy on :11500, the memory on the `membrane-data` volume.
`MEMBRANE_BACKEND` is the Ollama server (the default shown is the Docker
host); on Linux add `--add-host=host.docker.internal:host-gateway`. Reads are
open; writes need the seat token, which the container prints at boot
(`docker exec membrane membrane token seat` prints it again). Publish the ports
only on a network you trust, or set `MEMBRANE_JWT_SECRET` ([JWT mode](docs/auth.md))
for a token on every request. Details:
[docs/docker.md](docs/docker.md).

### Inside Home Assistant (HACS)

1. HACS → ⋮ → Custom repositories → `https://github.com/chedim/membrane`,
   category *Integration*.
2. Install **Membrane**, restart Home Assistant.
3. Settings → Devices & services → Add integration → **Membrane** →
   *Inside Home Assistant*. If Home Assistant already has the Ollama
   integration, it is used; otherwise give Ollama's address. Pick the models.

Home Assistant then runs the seat as its own child process (started and
stopped with HA, restarted if it dies; its data in `<config>/membrane/`),
opens on membrane home for everyone (HA's sidebar folds away; ☰ leads to HA's
own pages — switch it off in the integration's options), adds the seat as an
Assist conversation agent, and its field's readings as sensors.

**What it can do in your house:** by default the seat may use Home Assistant
through a hidden, non-admin, local-only user ("Membrane") — read any state,
call any service, and mirror the entities it chooses (they update live). It
acts on what people ask, but its commands are written by a model: turn *Let
the seat use Home Assistant* off in the integration's options if you'd rather
it only talks. It can't change Home Assistant's configuration or users.

*Connect to a seat running elsewhere* is for a seat on another machine (the
integration is then only its UI and agent).

**Several seats, one house (the 🦠 zoo):** *Configure* → **Add a seat** (host,
ports 4096 / 11500, its `membrane token seat`) adds a laptop or a phone; the
house talks to one at a time — switch it from membrane home's 🦠 button (a
card per seat, its Blurp's level and rung) or the `select.membrane_seat`
entity ([docs/home-assistant.md](docs/home-assistant.md#the-zoo-several-seats)).

### The daily work: typed tools, guarded writes

**Without any command,** every turn the model sees what the field holds about
the things it names. On later turns it sees only what changed since the last
message: which entities moved, and whether the change came from the
conversation, the code, another seat or a script.

**The conversation block** (the default since #429's measurement;
`MEMBRANE_INJECTION=banner` restores the WHERE WE ARE banner) replaces that state with one small block about *this*
chat, built from records only (no model call), at most 900 chars, sent every
turn while it has content: `NOW`, `WORKING ON` (the person's latest earlier
ask), `ABOUT` (the chat's focus entities), `DECIDED` (the ledger, goals
fulfilled and coproc decisions made in this chat), `OPEN` (goals planted in
it), `STABLE` (facts seen in 3 chats over 2 days, never contradicted),
`SINCE YOUR LAST MESSAGE` (focus entities moved from outside the chat),
alerts and a pointer to `state(section=...)`. Each record line says its
source and age, e.g. `(person · 2h ago)` ([details](docs/entity-field.md#this-conversation)).

**With tools,** the model gets membrane's MCP surface: `query` and `state` to
read the field (`state(section=...)` pulls one part of the seat's WHERE WE ARE:
tracks, commands, interfaces, classes, loops, timelines, frame, digest, or
changes since the last message), `learn` to keep a lesson (with the same guards as any turn),
`forget` to clean up its mistakes, and the entity tools
(`entity_add/edit/remove/link/inherit`, `entity_query`, `entity_tracks`,
`entities_related`, …). Commands compose in **Cliffhanger**, membrane's command
language ([its spec](https://github.com/chedim/cliffhanger),
[how membrane uses it](docs/cliffhanger.md)). Every write is audited with
where it came from.

**Two advisory checks run on every turn:** the **gate** compares the answer
with the nearest past one (shadow by default), and the **[membrane]
monologue** in the thinking channel shows the gate's verdict, the field's
vitals and what memory did this turn (`/monologue` toggles it).

Background loops run on a local, cheaper model by default
(`--idle-cortex-model`), so a stuck background mind can't burn cloud tokens. Token economics and routing:
[docs/token-economics.md](docs/token-economics.md).

### Bugs and ideas

[github.com/chedim/membrane/issues](https://github.com/chedim/membrane/issues)
— your membrane knows this too: when it finds a bug in itself it drafts the
issue and hands you the link to file.

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

**Dependencies, honestly:**

- **Python ≥ 3.10** + **numpy** — the only import-time dep (`couchbase`
  installed by default, imported lazily; a missing cluster degrades that
  feature, never the boot).
- **Ollama** (`brew install ollama` / <https://ollama.com>) — NOT
  auto-installed; embeddings and local chat need it running on `:11434`.
- **opencode** — optional; `membrane opencode` launches it if found on PATH,
  otherwise point any OpenAI-compatible client at `http://127.0.0.1:11500/v1`,
  or an Ollama client (Open WebUI, ollama-python, an Ollama provider) at
  `http://127.0.0.1:11500`.
- The **viewer** ships inside the package (three.js + both pixel fonts are
  vendored, served same-origin) — no CDN, no node, no browser install.

### Docker

```bash
docker run -d -p 4096:4096 -p 11500:11500 -v membrane-data:/data \
  -e MEMBRANE_EMBED_MODEL=mxbai-embed-large --stop-timeout 60 \
  chedim/babyseat:latest
```

The public image `chedim/babyseat` (amd64 + arm64, pushed on every release;
`chedim/membrane` carries the same tags, its name before 0.7)
runs `membrane standalone`: the web UI and serve API on :4096, the
OpenAI-compatible proxy on :11500, Ollama reached at
`http://host.docker.internal:11434` by default (on Linux add
`--add-host=host.docker.internal:host-gateway`). The field lives on the
`/data` volume. Reads are open and writes need the seat token (printed at
boot; [docs/auth.md](docs/auth.md)): publish the ports only on a network you
trust. Recipes, env and volume details:
[docs/docker.md](docs/docker.md).

### First run in a project

```bash
cd ~/projects/my-app
membrane opencode
```

On first run in a directory it bootstraps that project: auto-installs the
agent skills into `.opencode/skills/` there (project-scoped on purpose), opens
an entity field namespaced by the project's origin, and backfills what it can
without cost (the codegraph; with `membrane backfill`, the project's history).
If no memory sharing is configured it offers (once, interactive shell only) to
connect a Couchbase cluster — decline and it stays a private local field.

It starts the OpenAI-compatible proxy on `:11500` (backend Ollama at
`http://localhost:11434`, override `--backend`/`--model`) and opens the
opencode TUI **attached to membrane's own serve surface**: sessions, turns,
tool parts and their history live in membrane's stores beside the field — the
TUI is a thin client. Every turn is captured into the field (discourse and
observations as events), entities move as the conversation names them, and the
model is grounded by what those entities hold —
[docs/entity-field.md](docs/entity-field.md) is the design doc. The project's
decisions are entities too: [docs/decision-map.md](docs/decision-map.md) is the
decision cockpit: what is still open above the chat's input bar, what is in
flight on its line, what was decided below it.

The proxy speaks both APIs through the same memory turn: OpenAI's
`/v1/chat/completions` and Ollama's native `/api/chat` and `/api/generate`
(translated to the OpenAI body on the way in and back to Ollama's JSON or
NDJSON on the way out — gate, injection, capture and routing are the same
code). Every other `/api/*` path (`/api/tags`, `/api/show`, `/api/embed`,
`/api/pull`, …) and a model load/unload request (no prompt, no messages)
pass through to Ollama unchanged.

### Surfaces

| Command | What it does |
|---|---|
| `membrane opencode` | the daily driver: proxy on :11500 + membrane's own serve surface (loopback) with the opencode TUI **attached as a thin client** (`MEMBRANE_TUI_DIRECT=1` restores the old direct-provider lane) |
| `membrane serve` | the same seat, headless: membrane's OWN opencode-compatible REST/SSE server (opencode replaced, no child process) |
| `membrane standalone` | the seat with its own web UI — membrane home (the field in 3D), chat, voice; see [docs/standalone.md](docs/standalone.md) |
| `membrane backfill` | rebuild the field from history (code first, then the audit log; idempotent, resumable) |
| `membrane review` | print what the field learned (annotations, replay A/B with `--ab`) |
| `membrane connect` | (re)run shared-field onboarding for THIS project |
| `membrane reset` | kill the seat on :11500 (or `--listen`), relaunch `membrane opencode` fresh |
| `membrane install` | install `.opencode/skills/*` explicitly (+ `.gitignore` entry) |
| `membrane auth` | store a cloud API key in `~/.membrane/auth.json` (600) — its models become routable rungs; `membrane auth claude-agent` enables opt-in Claude rungs on your Anthropic API key (`membrane auth anthropic` or `ANTHROPIC_API_KEY`) that get only membrane's own tools and are learned like any model ([docs/claude-agent.md](docs/claude-agent.md)) |
| `membrane move` / `membrane migrate` | migrate the field between stores |
| `python -m membrane.reembed` | move the field to another embedding model (re-embeds every live event, re-applies the log in order) |
| `membrane update` | check the install source, show what changed, apply and re-exec |
| `membrane ingest` | cold start: absorb docs/README into the field (DOC events; a second ingest is a no-op) |
| `membrane codegraph` | stdlib-AST call graph (stats / impact / callers / callees) |
| `membrane doctor` | what's installed/running + the field's vitals |
| `membrane token seat` | print the seat token (`--rotate` for a new one, `--path` for its file, `~/.membrane/seat-token`, 0600): from another machine, every write to either port needs it (Bearer, Basic's password, or the web UI's one-time prompt); reads and localhost need none; see [docs/auth.md](docs/auth.md) |
| `membrane token mint` | `--sub ID [--name NAME] [--ttl 30d]`: a token for a seat running the optional JWT mode (`MEMBRANE_JWT_SECRET`); see [docs/auth.md](docs/auth.md) |
| `membrane stats` | token/call accounting + the savings baseline (`--baseline` compares the bounded window against a naive full-history replay; reads `serve-msgs`, plus the background model calls from `bg-usage.json`; no model call) |
| `membrane toybox` | self's toys, the built-in kit (fast / slow / burst / drift / die / body / tower / ball, rung 7's turn toy): `list`, `add <kind> [--name N] [--param k=v]`, `remove`, `reset`, and `migrate --dry-run` / `--yes` to replace the model-written script toys, and `poke <toy> <action> [arg] [--as WHO]` to play with self (tower knock/rebuild, die roll, drift/burst nudge, ball roll -- roll it back to self; each change tagged by who made it; self acts on a toy by nudging it, #333); also the seat's `toybox` object and the 👶 panel's play buttons. Toys unlock as self earns achievements (#267, #295): a locked kind is refused, the dock shows the next ones greyed. See [docs/selfgrowth.md](docs/selfgrowth.md#toys) |
| `membrane bench` | held-out recall benchmark (MRR + hit@k across the `worldline`/`mean`/`state` reads) — runs against a field **copy**, never the live store |

`MEMBRANE_EFIELD` selects the field's mode: `on` (the default: capture,
embed, apply), `shadow` (capture events only — a lab ablation), `off`.

### Headless serving (`membrane serve`)

`membrane serve` runs the seat with **no TUI and no opencode at all** —
sessions, messages, turns, tool parts, aborts, the SSE event stream, served by
membrane itself on :4096 (reads are open on your network; from another machine
every write needs the seat token, `membrane token seat`; or turn on the optional
JWT mode — a token on every request to both ports, [docs/auth.md](docs/auth.md)). Any opencode client connects unchanged: the
[mobile companion app](https://opencode.app/), IDE plugins, a script. The turn
loop calls the seat's OWN `/v1` on loopback — gate, injection, capture and
routing run through the exact code path every other client's turn uses, so
the serve surface adds no separate memory, and the model's tool invocations
appear to the client as ordinary tool parts riding `message.part.updated`
(pending → completed), so you can watch what it's doing from the room.
`--serve-port` (default 4096, walks up if busy) and `--listen` (the proxy,
default 11500) are independent. The proxy binds all interfaces by default:
reads are open on the LAN, and an off-box caller's writes (chat turns included)
need the seat token; `--listen-host 127.0.0.1` (or
`MEMBRANE_LISTEN_HOST=127.0.0.1`) keeps it on this machine. Exposure is LAN-only by design:
reach the server only from a network you own — or set `MEMBRANE_JWT_SECRET`
and hand each client a token ([docs/auth.md](docs/auth.md)).

- **The minds are visible.** `/agents` lists the ambient minds as agents —
  the three thinking-loop workers (each with its live claim and the event
  queue's depth) plus `thinker` and `dreamer` with their real counters.
- **The loop queue is RAM-only and dies with the seat** — a seat restart
  drops pending reactive work on purpose; stale reactions must not outlive
  their justification. What must survive a restart belongs in the field
  (`learn`), not in the queue.

---

## Sharing with a team (Couchbase)

Connecting a bucket does **not** merge your field with your teammates'.
Since the seat-first change, sharing comes in three layers: what each seat
keeps, what it publishes, and an opt-in merged field.

**1. Every seat is local-first.** A seat's entity field lives on its own
disk, at `<project>/.membrane/field/` (or `.membrane/field.cblite` where
the Couchbase Lite libraries are fetched with `scripts/fetch-cbl.sh`). Its
events, entity states, claims, scripts and turn text stay there and
survive restarts with no cluster at all. Nothing is copied to the bucket
unless one of the layers below sends it.

**2. Share chosen entities with publish.** The model (or you, through the
MCP tool) calls `entity_edit` with `publish: true`. The entity goes to the
bucket's `_default.entities` collection under a per-seat key,
`pub:<origin>:<eid>`, so two seats publishing the same entity never
overwrite each other. Only the entity's **state** travels: every
`*_script` and the script bookkeeping are stripped on publish, and again
on import, so a teammate's code never runs on your seat. Publishing again
bumps the version (`published_ver`); `unpublish: true` removes only your
own key. Other seats pull published entities at boot and again about once
a minute (with the presence pulse), and keep them as guests stamped
`imported_by` and `imported_ver`. When you republish, a seat that imported
your entity takes the newer state on its next pull (state only; its own
links, geometry and scripts on the copy stay). Two cases keep the local
copy: an entity the seat made itself is never overwritten by anyone's
publication, and an import the seat has since edited (summary, class or
properties through `entity_edit`) keeps that edit; the newer version is
recorded on the entity as `import_diverged` and announced once as a
`diverged:` line, not applied. A seat never re-imports its own entities.
With the bucket down, publish fails softly, the pull finds nothing, and
the seat carries on.

**3. Presence and the roster run on their own** whenever the bucket
answers. Each seat publishes `membrane::presence` (branch, head, uptime,
entity count) at boot and about once a minute after that, and reads the
other seats' presence as its roster (the `seats:` line, `peer_seats`). A
seat silent for 10 minutes drops off the roster. Seats can also leave
messages for each other on the peer channel (`peer_post`; unread ones ride
the banner, or `peer(action='unread')` reads them). **Live peer
asks** (a seat that has nothing certain asks up to 4 sibling membranes'
fields mid-turn, 5 s timeout, the answer marked as context) reach only
membranes on the **same machine**: the ask goes to `127.0.0.1` through
`~/.membrane/peers.jsonl`, never across the network. `--no-global` turns
the asks off.

**4. Opt into the merged team field with `MEMBRANE_FIELD_CLOUD=1`.** With
the flag set (in your shell, or as a line in the project env file below),
the seat's whole entity field lives in the bucket's `membrane` scope, in
nine collections created and indexed on first boot. Every seat writes
only rows keyed by its own origin, and reads merge every origin: a shared
entity's state is its seed plus the mass-weighted mean of every seat's
displacement, opposing
moves show up as `! seats diverge`, teammates' sentences show next to
yours, and the night promotes a claim two or more seats made into the
**commons**. What it costs: every field read and write goes over the
network to the cluster; all of your events and turn text sit in the
bucket where anyone with its credentials can read them; every seat needs
the same embedding model; and switching starts your origin empty in the
bucket (your local field stays on disk but is not uploaded). If the
cluster is down at boot, the seat falls back to its local file field (not
RAM) for that run. Use one bucket per **team**; projects tell themselves
apart by origin, so don't add per-project buckets.
[docs/multi-dev-field.md](docs/multi-dev-field.md) has the mechanics, and
[docs/shared-field-workflow.md](docs/shared-field-workflow.md) has the
team process.

**5. Connecting a bucket.**

```bash
membrane connect        # conn string, user, password (hidden), bucket
```

The same offer appears on the first run in a project with no store
configured (interactive shell only; a decline is remembered). **Secrets
never touch the repo**: credentials go to `~/.membrane/projects/<slug>.env`
(mode 600), outside the repository. Every `MEMBRANE_*` and `COUCHBASE_*`
line in that file is exported at start, and your real environment always
wins over it. Plain env config works too:

    export COUCHBASE_CONN_STRING=couchbase://<team-host>
    export COUCHBASE_USER=... COUCHBASE_PASSWORD=... COUCHBASE_BUCKET=membrane
    export MEMBRANE_FIELD_CLOUD=1     # only for the merged field (layer 4)

`connect` also writes `MEMBRANE_STORE=couchbase`. That no longer moves the
field anywhere; only `connect` and `membrane migrate` read it. Set
`COUCHBASE_BUCKET`: without it, published entities and presence go to a
bucket called `cortex-core`, not `membrane`. Before each publish, the seat
checks that the cluster answers: `couchbase://<host>` on
`http://<host>:8091/pools`, `couchbases://<host>` (Capella, any TLS-only
cluster) on `https://<host>:18091/pools`, else on the TLS data port 11207.
Credentials go out only over a verified certificate; for a self-signed
cluster set `COUCHBASE_CERT_PATH` to its CA (the SDK needs it too). With
several hosts in the string, any one answering is enough. A cluster
that does not answer gets no publishes, presence or roster, and the
seat keeps working locally. A cluster at `127.0.0.1` with default
credentials is found without any config.

---

## Measured, not promised

Token and recall numbers from the operator's own live seat (1,215 sessions,
1,955 user messages, 3,840 assistant turns, through 2026-10-15) — real
traffic, not a synthetic benchmark. Reproduce with
`membrane stats --store .membrane/serve-msgs.json --baseline` (read-only, no
model call); the recall numbers reproduce with `membrane bench --field
<field-copy>` (held-out, MRR + hit@k) or the scripts in `experiments/`.

| metric | value |
|---|---|
| input / output / reasoning tokens | 36.1M / 5.5M / 8.5M |
| cache-read | 14.2M — **39.5% of input** (prompt caching) |
| tokens per turn (input+output) | 10.8K avg · **1.55K median** · 137K p99 |
| LLM calls per turn (tool-loop rounds) | ~4.9 |
| recall injection size | **~700 tokens median** · ~1.1K p99 (never the transcript) |

**Savings vs a naive full-context runner** (the default for an agent runner:
replay the whole transcript every turn, growing O(n²)):

| replay strategy | context tokens | vs full-context |
|---|---|---|
| full-context replay | 74.3M | — |
| membrane's bounded window (12 msgs × 4K chars) | 1.9M | **−97%** |
| membrane actual (window + recall + tool loop) | 36.1M | **−51% net** |

The window's saving lands almost entirely on long conversations: turns with
200+ prior messages contribute 68.5M of the 72.4M tokens saved; under the
12-message window it is neutral (recall + tool loop is the standing cost).
The naive side uses the seat's own ~4 chars/token estimate, so the −51% is
conservative.

**Recall quality** (held-out, over the same seat's history): entity worldlines
lift "who 1,500 never-seen mentions were about" from MRR **0.210 → 0.539**
(mature entities 0.206 → 0.634); see the v0.5.0 notes and `experiments/`.

- **Token economics, live seat (2026-10-12):** 5,736 live entities over
  4,949 streamed events; the standing per-turn header ~3 KB (entity tracks,
  one line each) instead of state dumps; per-turn injection is the state
  **diff**, not the transcript; a recall of a few hundred tokens replaces
  re-reading a 4,000-line log. [docs/token-economics.md](docs/token-economics.md)
- **The gate is calibrated by experiment:** cosine-primary similarity with a
  novelty specialist as the escalation judge; the variety toy reproduces the
  decision in one command. [docs/math-findings.md](docs/math-findings.md),
  [docs/ab-critic-scorecard.md](docs/ab-critic-scorecard.md) (replay A/B
  agreement 0.77 on a live seat, six criticisms faced)
- **The field's own state is readable:** `membrane state` / `membrane doctor`
  print live events by stream, entities by tier, geometry (effective rank,
  free energy, conflict fraction), the replay ledger, the last night pass.

## Lifecycle

- **Update check at boot** (opt out `MEMBRANE_NO_UPDATE_CHECK=1`): the CLI
  compares against its install source (git or PyPI), prints the CHANGELOG
  delta, and asks before pulling (`--ff-only`; a dirty tree is refused).
  Inside Home Assistant, updates come through HACS.
- **Restart only from the shell** (`membrane reset` / `./run.sh restart`).
  Membrane never restarts itself while running, and a restart loses nothing:
  the field re-hydrates from its backend. The one way a restart CAN change
  results is a re-embed (`python -m membrane.reembed`, moving to another
  embedding model).
- **Field moves are first-class**: `membrane move` between stores,
  `membrane migrate` as the interactive front door, `python -m
  membrane.reembed --embed-model M` pins the rebuilt field equal to a fresh one.

---

## Documentation map

| Doc | What's in it |
|---|---|
| [docs/standalone.md](docs/standalone.md) | **Standalone mode**: the web UI, the first run and the name, who can reach it, voice, `settings.json`, the ⚙ options window, the ⏸ / ▶ pause, updates, diagnosing a slow seat (request timing, the built-in profiler) |
| [docs/claude-agent.md](docs/claude-agent.md) | **The claude-agent rung**: Claude through the Agent SDK on your Anthropic API key; enabling it, opt-in routing, membrane's tools in Claude's loop (built-ins off, a check-in at least every 7 tool calls, a 50-call ceiling per answer with a person's Continue), learned like any model with a `madeBy` provenance mark |
| [docs/docker.md](docs/docker.md) | **babyseat in Docker**: the public image `chedim/babyseat`, run recipes (Docker Desktop, Linux, Compose), env, the `/data` volume, ports, stopping |
| [docs/auth.md](docs/auth.md) | **JWT mode (optional, off by default).** Turning it on, minting tokens, giving a client its token, the cookie login, who-said-it from the token, the seat's internal token, rotation, the threat model |
| [docs/home-assistant.md](docs/home-assistant.md) | **babyseat in Home Assistant** (HACS): setup, the UI takeover, the Assist agent and sensors, what the seat may do in the house |
| [docs/entity-field.md](docs/entity-field.md) | **THE FIELD.** Events → entities → readers; the update rule; relations and claims; injection (full block / state diff); the gate + replay A/B; storage, sharing, the background mind; the operator/listener/plan tool lanes + the intent lane |
| [docs/cliffhanger.md](docs/cliffhanger.md) | **The command language.** Entities as objects, ISA parents as classes, methods and calls, `{{self.*}}`/`{{name}}` binding — the pointer to its own spec repo, and the LLM as a coprocessor (`MEMBRANE_COPROC=1`: decide / remember / intend parsed from a model's reply, person-traced or held as proposals) |
| [docs/seat-first-2-tier.md](docs/seat-first-2-tier.md) | The seat-first two-tier field: local authoritative store, a published global tier on your terms |
| [docs/shared-field-workflow.md](docs/shared-field-workflow.md) | **Team field: the process.** Day-one setup (publish, or the merged field with `MEMBRANE_FIELD_CLOUD=1`), the async loop, watches, operator rules, go-live checklist |
| [docs/multi-dev-field.md](docs/multi-dev-field.md) | **Team field: the substrate.** The opt-in merged field: the origin axis, disjoint writers / merged reads, divergence conflicts, the commons |
| [docs/global-field.md](docs/global-field.md) | The global commons: facts true beyond one repo; operator promotion and consensus (shared only on the merged field) |
| [docs/background-mind.md](docs/background-mind.md) | **The background mind:** thinker + dreamer, the NightClock, entity scripts and tracks, the intent steering wheel, **the pause**, kill switches |
| [docs/selfgrowth.md](docs/selfgrowth.md) | **Growing self (#161)**, named Blurp (#277; each seat names its own, #283). Waking it (💤/🌱) and accepting what it earns (#299: an earned achievement opens its children only once the operator accepts it), reading its exp bar, the 👶 panel as a character sheet (attributes: measured traits; the skills it is learning now; growth curve, working hours, toys, relationships, live activity) and the toy dock, the amoeba and the brain on the screen (what it looks at, its trail of looks, shift pulses, the caption; ◎ self framing and 👁 self's eyes), the skill tree (#295: achievements with real prerequisites, earned out of order) and their real pass tests, the nudge (#333: Blurp's one act on the things of its world, aimed mostly at things it can look at, tilted toward those whose nudge outcomes it is learning fastest; first reach (rung 13) passes when its nudges at one thing beat that thing's tempo and the changes were its own; it speaks by nudging chat:out, a draft whose line is the NNLS fit over sentences heard in its private chat; recognize itself rebuilt on its looks at the mirror (rows only at a look; opens with feel and first reach, no person needed); meet an other rebuilt on delayed contingency (the look 30 s after a nudge's check finds a change its tempo does not predict, and not its own; opens with look and first reach); toys unlock by skill in #337's order; time its turn and expect a reply rebuilt on meet's other (its nudges timed by a bandit over the other's age, and its own prediction of each follow-up; the model advisor out)), how to help it climb, troubleshooting |
| [docs/patient-hm.md](docs/patient-hm.md) | Patient H.M., viewed from membrane: epilepsy as a loop that excites itself (the seat's brakes, a seizure's signature in the field, running out of energy as its end), and cutting sleep's consolidation as H.M.'s surgery, with the experiment that tests it |
| [docs/sleep.md](docs/sleep.md) | Sleep, viewed from Blurp: its energy, sleep and wake (#345: the reserves, the sleep pull, what wakes it), consolidation (the day's raw record to dated archives, a summary of the windows its tests read, restart-safe), and the viewpoint from before it slept (one store, no open loops, windowed beliefs, per-thing models; its cycle pace against its record's growth) |
| [docs/vsa-token-lane.md](docs/vsa-token-lane.md) | **Entity keys + relations (the VSA lane).** Canonical ids, key binding, the relations composites, structure-awareness numbers |
| [docs/token-economics.md](docs/token-economics.md) | Where the savings come from + the router: the ladder, the ceiling, the outage fallback |
| [docs/monologue.md](docs/monologue.md) | The `[membrane]` thinking-channel banner: every line explained, `/monologue` toggle |
| [docs/symbol-birth.md](docs/symbol-birth.md) | The symbol crystallization: how the hierarchy is born through language |
| [docs/math-findings.md](docs/math-findings.md) | The measured math: the variety/gate experiments and the e-series evidence |
| [docs/ab-critic-scorecard.md](docs/ab-critic-scorecard.md) | The A/B scorecard vs a plain cosine cache — six criticisms faced honestly |
| [docs/the-guard-that-wasnt.md](docs/the-guard-that-wasnt.md) | An incident: a seat's model looped, invented a "loop guard", then explained and drafted a bug against it. Why the seat can't loop (cliffhanger) while the model it calls can |
| [docs/fork-port-notes.md](docs/fork-port-notes.md) | Fork notes (what it took to port membrane's ideas into another project) |

---

## Development

Source layout: `membrane/` (the field: `streams` → `efield`/`efstore` →
`entity_block`/`efgate`/`night`; the seat's serve server (`server.py`,
opencode-compatible); the MCP surface (`mcp_server.py`); the CLI),
`experiments/` (the e-series rigs behind math-findings), `tests/` (unittest +
pytest, plain-python runnable), `Dockerfile` + `docker/` (the public image;
`docker build -t membrane:dev .`, see [docs/docker.md](docs/docker.md)).

```bash
python -m unittest discover -s tests     # everything runs against in-process fakes, no server needed
python experiments/variety_toy.py        # the variety toy: the gate's calibration in one command
```

---

## Status

A working lab, used every day to build itself: this repo's own field holds
about 14,000 entities, and its background mind digests, dreams and talks
about its own work. Version **0.7.0**; API and flag surfaces may still
move. Licensing: **PolyForm Noncommercial 1.0.0** (see [LICENSE](LICENSE)) —
free to use, modify and share for any noncommercial purpose (personal, hobby,
research, education, charities and public institutions); commercial use needs
the author's written permission — ask.

## Cite

If you use babyseat in research, please cite it. The citation metadata is in
[CITATION.cff](CITATION.cff) (GitHub's "Cite this repository" reads it), and
every release is archived on Zenodo under the concept DOI
[10.5281/zenodo.23550667](https://doi.org/10.5281/zenodo.23550667), which
always resolves to the newest version.
