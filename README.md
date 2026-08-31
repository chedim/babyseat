# membrane

![Membrane](./centered_uroboros.gif)

> A **text-based hypervector Ollama proxy hippocampus for opencode** that
> learns as you go, adds **reliable Couchbase-backed shared LLM memory**, and
> **orchestrates LLM calls** between local and cloud inference — or even
> **replaces the LLM completely** for mundane problems.

A **Python lab** (sibling of `rosetta-check`, the Java linter, and
`cortex-experiment`, the proposals/docs). It runs as a local HTTP server on
`127.0.0.1` in front of Ollama (or Anthropic) — technically a proxy, but that
is the mechanism, not the point. The point is the field.

> **Why that name?** The project began as one question — *how do you measure
> "variety" for a memory that decides absorb-a-cached-answer vs
> escalate-to-the-model?* — and the answer is what makes the memory above
> possible and is why it is called **membrane**: a selective, *active* boundary
> between lived memory (the inside) and the incoming world (the outside) that
> absorbs what it has the variety for and escalates the rest — a regulator whose
> telos was flipped from *control the output* to *absorb the world*. The full
> findings, implication, and name are in **[Why "membrane"](#why-membrane)**.

---

## What it does, concretely

- **Hippocampus for opencode.** `membrane opencode` in any project starts the
  field-backed proxy on `:11500` and opens opencode pointed at it. The field
  recalls past situations per turn (RAG), learns every turn (Q/A exemplars
  with a judge/demotion pipeline), and answers five canonical frames itself —
  **intercepts: no model call, never learned**:

      membrane: query the field for <topic>     read recall
      membrane: learn that <fact>               write a memory
      membrane: forget <what or which>          delete (indexes "3-7, 9",
                                                topics, "the last hour",
                                                "this session", "everything")
      field state                               memory health/stats
      membrane: suggest                         review scope suggestions for
                                                the cross-project commons

  The agent may also emit these frames in its own prose; reads arrive next
  turn as clearly-marked `[membrane-intercept]` blocks.

- **Text-based hypervectors.** Every situation and answer is embedded
  (local `mxbai-embed-large`, near-free) into a high-dimensional vector — the
  field's atoms. The expensive resource is LLM tokens, not embeddings, so the
  field **acts from its own memory by default** and the model is the
  escalation.

- **Orchestration.** Three verdicts per turn:
  - **REPLAY** — cosine ≥ `--replay-sim` (0.97) to a safe past turn → the
    stored answer, **zero model calls**. The LLM is replaced completely for
    the mundane.
  - **ESCALATE** — forward to the backend (local Ollama by default; the
    Anthropic adapter fronts the cloud for Claude Code).
  - **OBSERVE** — forward + learn, never replay a coding answer.
  Replay, intercepts, and the compactor's distilled notes mean a well-worn
  project runs long stretches with **zero** model calls.

- **Learns as you go.** Every turn becomes a Q/A exemplar. A re-ask votes
  against (`reask_count`), the model's "correct anyway" contests
  (`contest_count`), `safe = (reask − contest) < demote_threshold`. The raw
  store is permanent; only the derived `safe` changes. The **anti-collapse
  rule**: the model's first contest restores, a later contest is record-only —
  the boundary cannot collapse to the model side. Any judge error/timeout →
  ACCEPT (degrade toward the environment, the safe direction).

- **Couchbase-backed shared memory.** By default the field is a local JSON
  file. Set `MEMBRANE_STORE=couchbase` and the exemplar field moves to a
  Couchbase cluster (cortex-experiment's env convention:
  `COUCHBASE_CONN_STRING` / `COUCHBASE_USER` / `COUCHBASE_PASSWORD` /
  `COUCHBASE_BUCKET`; collection `membrane.exemplars`). Multiple membranes —
  other projects, other machines — then share one substrate **without erasing
  each other**:
  - every memory key is **namespaced by origin** (the field-id where it was
    born); deletes only ever touch own keys; foreign patches are read-only;
  - when two membranes earned **different answers for the same situation**,
    CAS arbitration runs: the version more salient to *this* membrane's own
    field masters the shared situation, the loser **defers** into
    `alternates` on the winner's doc — CAN-style, nothing is destroyed;
  - foreign memories whose situation is near-identical to one I hold
    **poke through** at boot (`veil_poke`, bounded sample);
  - unreachable cluster → transparent fallback to the JSON field.
  See `tests/test_shared_substrate.py` for the full contract.

- **Cold tier + momentum.** The compactor distills clusters of retired raw
  turns into one distilled note per cycle; `--archive` (default on) moves the
  raw turns OUT of the hot field into a cold tier (`<field>.archive.jsonl`, or
  a Couchbase `exemplars_archive` collection) — the hot field stays small,
  raw turns stay queryable via `membrane: query the field`. `--momentum`
  (default on) keeps a frictional EMA of recent turn embeddings and skews
  recall toward your live working context — cortex's "laser projector".

- **Global commons.** Facts true beyond one repo are promoted (by operator
  hand, or by cross-repo consensus via ledger shards) into a global field
  shared by all membranes on the machine — read-only top-up on recall,
  provenance-tagged, with residency drift toward whichever field actually
  uses a memory. See **[docs/global-field.md](docs/global-field.md)**.

- **Thermodynamics you can watch.** `effective_rank` (requisite variety),
  `free_energy` (safe-weighted vitality), `novelty` (off-manifold residual) —
  live numbers on `field state`, in the audit, and in `membrane review`.

---

## Launch

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

### Use it — in any project directory

```bash
membrane opencode
```

That's the whole workflow. On first run in a directory it bootstraps that
project:

- auto-installs the five agent skills (query/learn/forget/state/global-field)
  into `.opencode/skills/` there (project-scoped on purpose — an opencode
  launched elsewhere never sees them; skip with `MEMBRANE_NO_AUTOINSTALL=1`),
- creates the per-project memory data in `.membrane/` there (`field.json`,
  `audit.jsonl`, logs; add `.membrane/` to `.gitignore`).

It then starts the OpenAI-compatible proxy on `:11500` (backend Ollama at
`http://localhost:11434`, override `--backend`/`--model`) and opens opencode
pointed at it. Every turn is grounded by the field's recall and learned back —
that project accumulates its own long-term memory in `.membrane/`.

- Only explicit install (no autoinstall) or a refresh of existing skills:
  `membrane install` (add `--force` to overwrite). It also appends the
  `.membrane/` runtime files to `.gitignore` (create if missing) and prints a
  short AGENTS.md blurb to paste.
- Check what's installed/running: `membrane doctor`.
- Other surfaces: `membrane proxy` (bare proxy), `membrane anthropic`
  (Claude Code front door), `membrane tui`, `membrane review`, `membrane demo`,
  `membrane reset` (kill the port, start fresh). `./run.sh` still works
  in-repo and delegates to the same CLI.
- Without installing anything, one-off via uvx:
  `uvx --from "membrane @ git+ssh://git@github.com/chedim/membrane" membrane install`.
- Requires only Python ≥ 3.10 + numpy; embeddings/chat come from a running
  Ollama.

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

## How it saves tokens

Two honest facts up front. First: **in this architecture, learning is free** —
every turn is absorbed as a local embedding (~\$0), so the training data
accumulates without model spend. In a measured real working session on this
repo, **4,219 turn exchanges (~1.17M chars ≈ 290k tokens of traffic) were
learned as `teed-pair` exemplars at zero marginal model cost** — the capture
rides the turns that were happening anyway. The savings come from what the
field then *answers without asking a model again*:

1. **Intercepts (0 tokens).** Five canonical frames —
   `membrane: query the field…`, `field state`, `membrane: learn that…`,
   `membrane: forget…`, `membrane: suggest` — are computed from the live
   field and never see a model; the agent's own prose-embedded asks (thought
   intercepts) are answered the same way. Every such turn is a full LLM-turn
   avoided.
2. **Replay (0 tokens).** A settled repeat of a safe memory returns the
   stored answer verbatim above `--replay-sim` (0.97): verbatim-similarity,
   demotion-guarded, tool-call-excluded. Rare early, compounding as the
   field earns trust — in the measured session 1 of ~180 chat-escalations
   replayed (~180 tokens); the design intent is that a month-old field
   replays the boilerplate (the tenth "run the tests the same way" costs
   nothing).
3. **Compaction (fewer tokens per turn).** The idle-time Compactor distills
   clusters of retired raw turns into one note; later turns inject the small
   note instead of the raw exchanges on every single request. Measured: two
   compaction cycles on this repo distilled 8 exchanges into 2 notes — a
   per-turn context saving paid every call, forever after.
4. **Self-author mode (0 tokens per turn).** The extreme dial: the cell
   *recites* its nearest lived scene instead of calling the model, and the
   genesis cache amortizes one LLM call over ~8 world scenes (the virtual
   world runs this way end-to-end).
5. **Momentum + cold tier (indirect).** Momentum-skew raises first-shot
   recall precision (fewer clarifying re-asks); the cold tier keeps the hot
   field small so kNN stays fast *and* the retrieval surface stays small.

Where the money actually goes after all of that: **escalations** — genuinely
new situations. That is the router's job: route them local first, cheap cloud
next, expensive cloud when really stuck, and replay the ones that settle.

---

## How it works

### The field acts; the LLM escalates

The world is a **field**: an append-only store of exemplars, each a situation
+ answer pair with a local hypervector. The field is the default actor. The
LLM keeps two jobs, both rare:

1. **Answering escalations** — turns the field cannot absorb confidently.
2. **The judge** (on by default; `--no-judge` opt-out) — adjudicate demotions
   via the **judge ladder** (start one rung up the cost stack from the model
   that answered and propagate upward until a rung rules; the human is the
   top rung, asked with question + options + consequences per case).

Recall is **momentum-skewed kNN** (toward the live working context), the
context block is MMR-deduped, and replay returns the stored answer verbatim
above `--replay-sim`. On escalations, the **router** picks the model:
`score = fit_hat − λ_time·urgency·delay − λ_cost·price` — the cost ladder
(local 0 → cheap cloud → expensive cloud) with a measured pressure valve
(cloud only when the machine is genuinely slow or no local fits), a daily
budget cap, eps-exploration of unsampled locals, and per-turn audit lines
(`route: model=… reason=… pressure=…`) that `field state` tabulates as
claimed-vs-measured win rates.

### The compactor and the cold tier

A daemon (the `Compactor`) runs while the session is **idle**: it clusters
safe retired raw turns, distills each cluster into one note the distilled
model writes (`--compact-model`, default: follows the model selected in
opencode), archives the raw turns to the **cold tier** (JSONL or Couchbase),
and keeps the hot field small. The note is the field's living knowledge; the
archive is the provenance — queryable, never forgotten.

### Shared substrate on Couchbase

See [What it does](#what-it-does-concretely) for the capsule version; the
mechanics are in `membrane/field.py` (CAS arbitration, origin-namespaced
keys, veil poke) and pinned by `tests/test_shared_substrate.py`.

### The global commons

`--global-field [PATH]` (default `~/.membrane/global-field.json`), kill switch
`--no-global`. Local fields are the actor; the commons is a read-mostly file
layer the operator's hand writes. Full design: **[docs/global-field.md](docs/global-field.md)**.

### Measuring variety

`effective_rank` (entropy of the covariance eigenvalue spectrum), `spread`
(mean pairwise cosine distance), `novelty` (the query's off-subspace
residual). The settled conclusion: **cosine is the primary gate; the
null-space novelty is a narrow low-rank specialist** — it earns a role only on
genuinely low-rank fields (`effective_rank / N < 0.5`). See
`docs/math-findings.md` and `../cortex-experiment/membrane.md` §2.

---

## The thermodynamic principle — "only variety absorbs variety"

This is the project's principle, in plain words: Ashby's Law of Requisite
Variety with the control-theory skin peeled off. Control theory's telos is
*anti-variety* — the regulator exists to **collapse** a disturbance's variety
into a controlled, low-variety output ("destroy variety"). The membrane inverts
the ethics: variety is not the threat to be contained, it is the **life to be
grown**. `free_energy` (safe variety) is the field's vitality — a field with no
safe variety is inert (it escalates everything, acts on nothing: dead). Same
law, opposite telos: control theory says "grow enough control variety to *keep
the output controlled*"; the membrane says "grow enough field variety to *absorb
more of the world*."

In the code:

- **`effective_rank`** = the field's requisite variety, *measured* not assumed.
  A query off the experienced manifold (high `novelty`) can't be absorbed →
  escalate. Only a field with variety *in that direction* can absorb a query in it.
- **`free_energy`** = `(safe fraction)·effective_rank − ¼·(unsafe
  fraction)·rank` — the *one number* that says whether the field is getting
  richer or just bigger. Surfaced in `field state` and `review.py`.
- **adaptive temperature** (`--variety-temp`, default off) is the field
  *spending* its requisite variety: `sim_min` eases toward `--sim-min-floor`
  (0.70, the validated safe cutoff) as `free_energy` grows. "Only **safe**
  variety absorbs variety" — a junk field pays the disorder tax, keeps
  `free_energy` low, and stays conservative.
- **the veil** (`--nudge-sigma`, default 0 = identity) is the per-instance
  perturbation of the incoming situation embedding: Gaussian noise added then
  renormalized, applied to everything that transits this membrane. The
  forwarded prompt text is **never** garbled; only the field's representation
  is veiled. A low-variety workload can't grow a varied field on its own; the
  nudge injects the variety it lacks.
- **multi-veil** — "run multiple of them" is **decentralized**, not
  orchestrated. No central input, no live router (a central oracle is a rank-1
  field — one perspective — and rank-1 cannot absorb variety). Each membrane is
  autonomous — own field, own audit, own nudge seed, own temperature — pulling the
  same public workload. `--tag <name>` defaults the field/audit paths.
  Comparison is **offline**, reading each membrane's saved audit afterward.

```bash
PYTHONPATH=. .venv/bin/python experiments/multi_veil.py   # four autonomous membranes, one workload; compare free_energy offline
```

---

## Where it came from

membrane's first job was an open question the design left hanging — *how do we
measure "variety" for the Membrane process?* — and a confidence gate to decide
absorb-a-cached-answer vs escalate-to-the-LLM. That phase is built and settled,
and it is still in the code as the substrate the memory is built on. It is
recorded in detail in `../cortex-experiment/membrane.md` (the cross-project
findings) and `docs/math-findings.md` (the three experiments + the
self-correcting conclusion). The confidence gate (cosine-primary,
reversibility-guarded, conservative bootstrap), the re-ask training loop, the
opt-in judge, and the variety math are all described above — they are the
hippocampus; the world below was the scaffolding that proved them.

---

## Run the toy

```bash
cd ~/projects/membrane
pip install -r requirements.txt   # numpy only
python experiments/variety_toy.py
```

Expected: the low-variety store has low effective-rank and low spread; a query
off its manifold reads as highly novel (gate escalates), while the same query
against the high-variety store reads as in-manifold (gate acts).

---

## 🤿 Easter egg: a self-authoring virtual world

The same field, worn as a world. Point the proxy at a fictional premise and it
grows a **self-authoring world on its own clock** from a memory of scenes; you
inhabit it as a voiced protagonist — your words are your character's thoughts,
the narration speaks from the scenes that character lived, and your thoughts
steer the character's state directly. Three.js stars = scenes, a walking cast,
save/load/new worlds. It is the original realization of the field-first idea
(kept, playable, and documented in
[`docs/exploration-game.md`](docs/exploration-game.md)) — the coding-agent
hippocampus above is where the project went next.

```bash
cd ~/projects/membrane
PYTHONPATH=. .venv/bin/python -m membrane.proxy --listen 8770 \
  --backend http://127.0.0.1:11434 \
  --system "The dimension-hopping misadventures of Rick and Morty. Rick is a young, broke, bitterly brilliant garage scientist who builds portal technology from salvaged junk and runs cons across realities to keep the lights on; Morty is his anxious fourteen-year-old kid brother, dragged along on every scheme. They portal between fractured worlds — dead planets, weird markets, broken civilizations — barely escaping what Rick's hubris stirs up. Dark, fast sci-fi: cynicism and wonder tangled, danger played for gallows humor, affection buried under contempt. Narrate their adventures; Rick drives, Morty worries, things go sideways." \
  --warmup-model gemma3:12b --entropy-ceiling 0.05 \
  --self-author --no-judge --viewer \
  --protagonist "Rick — a young, broke, bitterly brilliant garage scientist who builds portal tech from junk and runs cons across dimensions to make rent; nihilistic and grandiose, drinks to keep the quiet out, buries affection under contempt — clipped, rapid, sarcastic sentences; names the ugly truth nobody asked for; a running bitter mutter to himself; never admits he cares" \
  --character "Morty — Rick's anxious fourteen-year-old kid brother, dragged into every scheme; earnest, stammering, the one with a conscience — hesitant, runaway sentences that trip over themselves; asks the question Rick won't; brave only when someone's about to get hurt" \
  --consolidate --feed-rate 4 --cpu-target 0.3 \
  --field worlds/story-field.json --audit worlds/story-audit.jsonl
# open http://127.0.0.1:8770/viewer and hit PLAY.
```

Key flags: `--system` (world premise), `--protagonist` / `--character` (voiced
cast), `--self-author --no-judge` (LLM out of the live loop), `--consolidate`
(the world's own clock), `--viewer` (3D cosmos + `/state`). **A fresh world
needs seeding** — arm the clock or press STEP before expecting chat replies;
replies are recited field memory, not model output.

> **Want to just play?** [`docs/exploration-game.md`](docs/exploration-game.md)
> is a player's manual — the screen, the three world-drive modes, the controls,
> and how to explore — written for a gamer, not an engineer.