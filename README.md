# membrane

![Membrane](./centered_uroboros.gif)

> A **text-based hypervector Ollama proxy hippocampus for opencode** that
> learns as you go, shares a Couchbase-backed team memory, and **orchestrates
> LLM calls** between local and cloud inference — or **replaces the LLM
> completely** for mundane problems.

A **Python lab** (sibling of `rosetta-check`, the Java linter, and
`cortex-experiment`, the proposals/docs). It runs as a local HTTP server on
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
field, and the daily loop. What the field does under the hood, how sharing
works, and the project's history are in the sub-documents (see
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
  - [Run the toy](#run-the-toy)
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

Requires only Python ≥ 3.10 + numpy; embeddings/chat come from a running
[Ollama](https://ollama.com). Without installing anything, one-off via uvx:
`uvx --from "membrane @ git+ssh://git@github.com/chedim/membrane" membrane install`.

### First run in a project

```bash
cd ~/projects/my-app
membrane opencode
```

That's the whole workflow. On first run in a directory it bootstraps that
project:

- auto-installs the five agent skills (query/learn/forget/state/global-field)
  into `.opencode/skills/` there (project-scoped on purpose — an opencode
  launched elsewhere never sees them; skip with `MEMBRANE_NO_AUTOINSTALL=1`),
- creates the per-project memory data in `.membrane/` there (`field.json`,
  `audit.jsonl`, logs; `membrane install` appends `.membrane/` to
  `.gitignore`),
- if no memory sharing is configured, offers (once, interactive shell only)
  to connect a Couchbase cluster for this project — decline and it stays a
  private local field (see
  [Sharing a team field](#sharing-a-team-field-couchbase)).

It then starts the OpenAI-compatible proxy on `:11500` (backend Ollama at
`http://localhost:11434`, override `--backend`/`--model`) and opens opencode
pointed at it. Every turn is grounded by the field's recall and learned back —
that project accumulates its own long-term memory in `.membrane/`.

### Daily workflow

Inside opencode, the field answers five canonical frames itself —
**intercepts: no model call, never learned**:

    membrane: query the field for <topic>     read recall
    membrane: learn that <fact>               write a memory
    membrane: forget <what or which>          delete (indexes "3-7, 9",
                                              topics, "the last hour",
                                              "this session", "everything")
    field state                               memory health/stats
    membrane: suggest                         review scope suggestions for
                                              the cross-project commons

The agent may also emit these frames in its own prose; reads arrive next turn
as clearly-marked `[membrane-intercept]` blocks. Write authority stays human:
model prose never writes the field.

Everything else is ambient: recall grounds each turn, replay answers settled
repeats from memory (zero model calls), and the router picks the cheapest
eligible model under the ceiling you picked in opencode. How the token
economics and routing work: [docs/token-economics.md](docs/token-economics.md).

### Other surfaces

| Command | What it does |
|---|---|
| `membrane tui` | TUI + proxy on :11435 (tears the proxy down on exit) |
| `membrane proxy` | bare proxy, foreground |
| `membrane opencode` | field-backed opencode on :11500 (the daily driver) |
| `membrane anthropic` | TUI+proxy in `--anthropic` mode (Claude Code front door) |
| `membrane review` | print what the field learned |
| `membrane demo` | in-process end-to-end loop (no chat model needed) |
| `membrane connect` | (re)run shared-field onboarding for THIS project |
| `membrane reset` | kill the proxy on the port, start fresh |
| `membrane install` | install `.opencode/skills/*` + AGENTS.md blurb explicitly |
| `membrane doctor` | what's installed/running (CLI, skills, Ollama) |

`./run.sh` still works in-repo and delegates to the same CLI.

### Claude Code (Anthropic cloud)

```bash
ANTHROPIC_BASE_URL=http://127.0.0.1:11435 ./run.sh anthropic
```

Observation-only mode: coding answers are never replayed (always forward +
learn + label; the gate's would-be absorb is audited as `would_absorb`).
Ambient quality signals (tool-error, re-ask-after-escalate) make the judge
fire from *what happened*, not a button. The `ANTHROPIC_API_KEY` is
passthrough only: read from the client's `x-api-key` header per request,
forwarded to `api.anthropic.com`, and **never stored on the instance, written
to the field or audit, or printed**. The judge stays Ollama on
`localhost:11434` even in `--anthropic` mode — **local judging for cloud
inference**.

---

## Sharing a team field (Couchbase)

By default the field is a private local JSON file. To make a team share one
substrate — same project, different machines, one memory — point every seat at
the same Couchbase cluster. One bucket per **team**, `membrane` scope,
`exemplars` collection; projects distinguish themselves by the origin axis (do
NOT add per-project buckets — that would defeat independent-agreement
promotion).

### `membrane connect` (recommended)

Run in the project directory; answer four prompts:

```bash
membrane connect
#   conn string   [couchbase://localhost]
#   user          [Administrator]
#   password      (hidden)
#   bucket        [membrane]
```

The same offer appears automatically on the first `membrane opencode` /
`membrane tui` in a project that has no store configured (interactive shell
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
cluster → transparent fallback to the JSON field (degradation, never failure).

### What sharing guarantees

Mechanics that make sharing collision-free (all pinned by
`tests/test_shared_substrate.py`; details in
[docs/multi-dev-field.md](docs/multi-dev-field.md)):

- every memory key is **namespaced by origin** (the field-id where it was
  born); deletes only ever touch own keys; foreign patches are read-only;
- two membranes earning **different answers for the same situation** → CAS
  arbitration: the more salient version masters the situation, the loser
  **defers** into `alternates` — CAN-style, nothing is destroyed;
- foreign memories near-identical to one you hold **poke through** the veil
  (cosine ≥ 0.90) as marked context at boot;
- teammates online get **live peer asks** on escalations (bounded fan-out,
  LAN/VPN); `peers: N live` at boot.

The full team process — day-one setup checklist, the async work loop across
time zones, the watch layer, operator rules — is
**[docs/shared-field-workflow.md](docs/shared-field-workflow.md)**.

---

## Checking on the field

- `field state` (in opencode) — backend, field composition, thermodynamics
  (`effective_rank`, `free_energy`), gate knobs, compactor counters.
- `membrane review` — what the field learned, distilled notes included.
- `.membrane/audit.jsonl` — the full per-turn audit trail (routings, judges,
  operator hands).

What the numbers mean and how the field's "thermodynamics" work:
[docs/principle.md](docs/principle.md) (plain words) and
[docs/math-findings.md](docs/math-findings.md) (the measured math).

## Easter egg: a self-authoring virtual world

The same field, worn as a world: point the proxy at a fictional premise and
it grows a **self-authoring world on its own clock**; you inhabit it as a
voiced protagonist. Kept, playable, documented — **[docs/exploration-game.md](docs/exploration-game.md)**
(player's manual) covers the screen, controls, and world-drive modes, and
shows the launch command.

---

## Documentation map

| Doc | What's in it |
|---|---|
| [docs/shared-field-workflow.md](docs/shared-field-workflow.md) | **Team field: the process.** Day-one setup, the async loop, watches, operator rules, go-live checklist |
| [docs/multi-dev-field.md](docs/multi-dev-field.md) | **Team field: the substrate.** Origin axis, veil, arbitration — what makes multi-seat sharing collision-free |
| [docs/global-field.md](docs/global-field.md) | The global commons: facts true beyond one repo; operator promotion, consensus, residency drift |
| [docs/token-economics.md](docs/token-economics.md) | Where the savings come from (intercepts, replay, compaction) + the router: the ladder, the ceiling, the outage fallback |
| [docs/principle.md](docs/principle.md) | "Only variety absorbs variety" — Ashby's law with the telos flipped; free_energy, effective_rank, the veil, multi-membrane |
| [docs/math-findings.md](docs/math-findings.md) | The substrate-phase math: three experiments, one self-correcting conclusion (cosine primary, novelty specialist) |
| [docs/ab-critic-scorecard.md](docs/ab-critic-scorecard.md) | The A/B scorecard vs a plain cosine cache — six criticisms faced honestly |
| [docs/exploration-game.md](docs/exploration-game.md) | The easter egg: membrane as a self-authoring exploration game (player's manual) |
| [docs/post-restart-plan.md](docs/post-restart-plan.md) | Working notes: routing-fix verification and the roadmap |
| [cycled_environment.md](cycled_environment.md) / [endless_environment.md](endless_environment.md) | The theory notes behind the principle (the loop, and the trap unbounded variety sets) |

---

## Development

Source layout: `membrane/` (proxy, field, router, opencode host, TUI),
`experiments/` (variety toys, multi-membrane rigs), `tests/` (unittest,
plain-python runnable), `worlds/` (saved easter-egg worlds).

### Run the tests

```bash
python -m unittest discover -s tests     # or: .venv/bin/python -m unittest discover -s tests
```

No server needed: the couchbase-substrate and router tests run against fakes
in-process.

### Run the toy

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
field learned the codebase while it was being written). Not yet
version-released; API and flag surfaces may move. **Private project** — no
license yet; all rights reserved by the author.