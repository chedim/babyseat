# membrane

![Membrane](./centered_uroboros.gif)

A **local-first, self-authoring world you enter as a character.** It grows a
world on its own clock from a memory of scenes; you inhabit that world as a
voiced protagonist — your words are your character's thoughts, the narration
speaks from the scenes that character lived, and your thoughts steer the
character's state directly. It is a world you **probe by association, walk as a
graph, steer by pruning, and live inside** — not a chatbot, not a game, not a
search box.

It is a **Python lab** (sibling of `rosetta-check`, the Java linter, and
`cortex-experiment`, the proposals/docs). It runs as a local HTTP server on
`127.0.0.1` — it does front an Ollama (or Anthropic) model server, so it is
technically a proxy, but that is the mechanism, not the point. The point is the
world.

> **Why that name?** The project began as one question — *how do you measure
> "variety" for a memory that decides absorb-a-cached-answer vs
> escalate-to-the-model?* — and the answer is what makes the world above
> possible and is why it is called **membrane**: a selective, *active* boundary
> between lived memory (the inside) and the incoming world (the outside) that
> absorbs what it has the variety for and escalates the rest — a regulator whose
> telos was flipped from *control the output* to *absorb the world*. The full
> findings, implication, and name are in **[Why "membrane"](#why-membrane)**.

---

## Executive summary

- **What it is.** A world server: a growing memory field of scenes + a cast of
  named characters, served through a 3D viewer you can walk and a chat that
  reads like a novel.
- **The field is the actor; the LLM is a gardener.** The expensive resource is
  LLM tokens, not the embedding (local Ollama, near-free). So the world **acts
  from its own memory by default** — your chat replies are *recited* from the
  nearest lived scene, not generated. The LLM only **writes the chronicle** in
  rare, cached chapters (the "gardener") and is the escalation when the field is
  not sure. In `--self-author` mode the LLM is **out of the live loop entirely**:
  zero model calls per chat turn.
- **You are a character.** `--protagonist "Name — role — voice"` writes you a
  voiced protagonist. Your typed messages render as that character's *thoughts*;
  the reply is *narrator prose* spoken from the scenes your character lived.
- **Your thoughts are the ground.** Each thought unconditionally walks your
  character's state toward it — the field *follows* you, it doesn't check you.
  This is the user-as-protagonist replacing a model consistency gate.
- **It grows on its own clock.** `--consolidate` arms a daemon that feeds the
  world from a cached chronicle (one LLM call → ~8 scenes) and digests (SVD /
  novelty) on its own wall-time. PLAY/PAUSE, a feed-rate dial, a CPU-target
  slider.
- **Characters are walking regions of the field.** Each character carries a
  `state_vec` — an EMA of the scenes tagged to them. They are not rows in a
  table; they are regions of the field you can fly to on the map.
- **Explore + persist.** A Three.js cosmos (stars = scenes, PCA-projected), a
  neighbourhood panel to walk the association graph, and save / load / new world.

> **Honest origin.** membrane *began* as a Claude memory-gate proxy (a
> confidence gate to decide absorb-a-cached-answer vs escalate-to-the-LLM,
> calibrated by a "measure variety" math). That gate, the field, and the variety
> math are still the load-bearing substrate — but the project grew into the
> world above, and the variety math became the **thermodynamics of the world's
> growth** (variety = the world's life; `free_energy` = its safe-weighted
> vitality; "only variety absorbs variety" is Ashby's Law with the control-theory
> telos inverted). The world is the realization; the gate was the scaffolding.
> That history is recorded in **[Where it came from](#where-it-came-from)** and
> in `../cortex-experiment/membrane.md`.

---

## Launch (Rick & Morty)

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

The key flags:

| flag | what it does |
|---|---|
| `--system` | the **world premise** — the constant ground the LLM narrates from |
| `--protagonist "Name — role — voice"` | writes **you** a character to be (admits it, sets it POV) |
| `--character "Name — role — voice"` (repeatable) | seeds the **ensemble** around the protagonist |
| `--self-author --no-judge` | **LLM out of the live loop**: the cell speaks from field memory, no model call per turn; the judge is a no-op |
| `--consolidate --feed-rate 4 --cpu-target 0.3` | arms the **world's own clock** (self-feed + digest) |
| `--viewer` | serves the 3D viewer + `/state` snapshot |
| `--field` / `--audit` | where the world's memory + decision log persist |

You **are Rick**: your words render as Rick's thoughts, the narrator speaks from
Rick's lived scenes, Morty is the co-lead, and the world is *about them*.

> **Want to just play?** [`docs/exploration-game.md`](docs/exploration-game.md) is
> a player's manual — the screen, the three world-drive modes, the controls, and
> how to explore — written for a gamer, not an engineer.

> **A fresh world needs seeding.** With the cast pre-seeded the field starts empty
> and grows from **feeds** (the consolidator, or the viewer's STEP). Until it has a
> few scenes the chat has nothing to recall — so arm the clock and let it run (or
> press STEP a few times) before you expect replies. Chat replies are **recited
> field memory**, not model output.

---

## Use in another project (portable membrane)

membrane is pip-installable straight from this repo, so any project can get a
field-backed coding agent — its own memory field, its own skills, zero copies
of this repo needed.

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

- auto-installs the four agent skills (query/learn/forget/state) into
  `.opencode/skills/` there (project-scoped on purpose — an opencode launched
  elsewhere never sees them; skip with `MEMBRANE_NO_AUTOINSTALL=1`),
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
- Other surfaces: `membrane tui` (world + proxy), `membrane proxy`,
  `membrane anthropic` (Claude Code front door), `membrane review`,
  `membrane demo`, `membrane reset` (kill the port, start fresh). `./run.sh`
  still works in-repo and delegates to the same CLI.
- Without installing anything, one-off via uvx:
  `uvx --from "membrane @ git+ssh://git@github.com/chedim/membrane" membrane install`.
- Requires only Python ≥ 3.10 + numpy; embeddings/chat come from a running
  Ollama.

---

## How it works

### The field acts; the LLM gardens

The world is a **field**: an append-only store of scenes, each a short chronicle
passage with a local `mxbai-embed-large` vector. The field is the default actor.
The LLM keeps exactly two jobs, both rare:

1. **Gardener** — write the chronicle in big cached chapters (one call → ~8
   scenes). It grows the *content*; the field supplies the *structure* (which
   memories associate, what survives your pruning).
2. **Escalation** — adjudicate demotions (the judge, opt-in; default off via
   `--no-judge`) and the one-time seed.

`--self-author` (the running mode) takes the LLM out of the live loop entirely.
Every turn — act *and* escalate — is **the cell speaking from its own memory**,
no model call, nothing learned. The field is coupled to the *user*, not the
model: your re-ask is the only channel back in, and it demotes the cell that
spoke. The **SPEAK organ** (`runtime.Membrane.generate`, selected by `--speak`):

- **`recall` (default)** — the cell RECITES its most relevant lived scene: the
  nearest stored scene to the query (kNN). The field is grown from cohesive
  LLM-written chronicle passages, so the nearest neighbour IS a cohesive,
  on-topic story. Legacy telegraphic fragments (`< _COHESIVE_MIN_WORDS`) are
  skipped.
- **`ngram` (opt-in)** — a fresh stochastic draw from a word-trigram trained on
  the field's own stored actions. More "generative" but incoherent past a few
  tokens; an escape hatch.

### The world's own clock — consolidation

A daemon thread (the `Consolidator`, `membrane/consolidate.py`) runs the world
independently of any viewer. When armed (`--consolidate` or the viewer's PLAY),
each cycle has two phases:

- **FEED** — the world feeds itself `feed_rate` times from the **genesis cache**.
  Each feed is a cached chronicle scene → a cheap local embed + absorb, *not* a
  model call. `feed_rate` is a continuous float (0–32/cycle); the fractional tail
  resolves probabilistically. **This is the dial that controls model use:** rate
  > 0 makes real embed calls every cycle; the model itself is called only when
  the cache runs low.
- **DIGEST** — CPU consolidation, no model, no field mutation: refresh the global
  `thermo_state` (one SVD), sweep a `budget` of cells recomputing each one's
  `novelty` (one SVD per cell), and rebuild the `FieldNGram` when the field grew.

The `budget` is ramped by a **closed loop** targeting `cpu_target` — the
fraction of each cycle's wall time spent digesting. So the CPU slider truly
ramps consolidation against a real duty fraction. `consolidation_stats`
(cycles / cpu_actual / cpu_target / swept / budget / mean_novelty / eff_rank /
free_energy / fed / cache / stories) is echoed on `/state` and the viewer.

**Genesis cache — one LLM call feeds the world ~8×.** The chapter generator asks
the LLM *rarely* to narrate the chronicle as a batch of `--CHAPTER`=8 substantial
scenes separated by a `---` line; the scenes are cached on `srv.story_cache`.
Each FEED pops one cached scene (cheap `/api/embed` + absorb); the cache refills
with one model call when it drops below `_LOW_WATER`=3. The chronicle continues
from `srv.story_history` for a cohesive running narrative. Growth is
**rate-controlled** (the feed-rate dial + the cache), not variety-capped; the
story `temperature` is fixed at 0.7 server-side.

### Characters are walking regions of the field

The world grows a bigger memory, but it has no persistent *entities* — no
people, no state, no causality — without a **character spine** (`membrane/cast.py`).
The chronicle's scenes tag to *characters*, and each character carries a
persistent `state_vec` — an EMA of the scenes tagged to it, the tip of its walk
through the field. **A character is not a row in a table; it is a walking region
of the field.**

- **Geometric tagging** — a scene tags to every character whose `state_vec` it
  sits close to (cosine ≥ `admit_threshold` 0.40); each tagged character's
  `state_vec` EMA-walks toward the scene (`decay` 0.6). **No auto-admit**: a
  scene far from the whole cast is left untagged, so the cast stays short
  (capped 12; admission is seed-or-manual).
- **Genesis seed** — the *first* chronicle chapter (empty cast) emits a `cast:`
  block naming the ensemble; one batched embed admits each, then a one-time
  `retro_tag` colors the *entire* existing field by character immediately.
- **The roster (Esc/⚙ menu)** — an RPG-style cast list: name, role,
  `scene_count · last_cycle`, a colour dot; **Retire** removes a character (its
  stars revert to neutral) and **Pin** protects one. Click a row → the camera
  flies to that character's region. `POST /cast` (`{action: retire|pin|admit|pov,
  id, name?}`) is the menu's endpoint.
- **Cast marks on the map** — each character's projected `state_vec` is marked
  and labelled; stars are tinted by their `tags[0]` from a 12-colour palette.
  The cast persists in a sibling `-cast.json` file; old field files load cleanly.

### You are a character — the narrator + the I as ground

The narrator makes the chat a **novel**: you *are* a character (a protagonist
with a defined voice), your typed messages render as that character's
**thoughts**, and the narration speaks *from that person* — in `--self-author`
mode by reciting the scenes that character **lived** (the field memory tagged
to them).

- **A character with a voice** — `Character` carries `role` and `voice`. The
  genesis SEED authors `Name — role — voice` per character and designates a
  **protagonist** with a leading `★`. You can also inject one with
  `--protagonist` (admits at boot if absent, sets POV, retro-tags the existing
  field so the protagonist colors it at once).
- **The narrator speaks from that person** — `generate()` restricts recall to
  the POV character's **tagged** exemplars when a POV is set, so the recited
  scene is one the protagonist *lived* — the narration comes from their region of
  the field, which *is* their voice. It falls back to the global nearest when the
  protagonist has fewer than three tagged scenes (they haven't lived enough yet).
- **The chat reads as a novel** (viewer) — when a POV is set, your messages
  render as the protagonist's **thoughts** (italic, indented, `❝ name · …`); the
  reply renders as **narrator prose** (a left border, no attribution). The chat
  header shows the protagonist's name; the input prompt becomes *what does
  {name} think?*. The cast roster has a **POV** button per row (POST `/cast
  {action:"pov",id}`) and a `★ POV` badge — click it to switch whose eyes the
  world is seen through. No POV → the chat stays green/white.
- **Persistence** — `pov_id` rides in the `-cast.json` wrapper; old bare-list
  cast files load with `pov_id=None` (backward-compatible).

**The I as ground.** The narrator says "you ARE the character" — but your
thoughts must actually shape the character, or the gardener's scenes define it,
not you. When a POV is set, each of your chat thoughts **unconditionally**
EMA-walks the POV's `state_vec` toward it (`Cast.walk_pov`, wired into
`gate_turn` — the one mode-independent point every message passes through),
bypassing the cosine `admit_threshold` that gates every other scene. You ARE the
POV character, so your thought moves the character *by fiat*, not by similarity.
**The field follows the I; it doesn't check it.** No exemplar is appended (no
recall loop, no echo of your own words), no model call — one cheap EMA update per
thought. This is the foundation of **replacing an LLM consistency gate with the
I-grounded field**: the model's "would this character do this?" is a *generic,
parametric* character — not your specific, voiced one — so it would flatten your
character back toward the archetype. The model stays the gardener; the user is
the ground; on the borderline the next thought resolves it, not a model call.

### The character-driven chronicle

A world that begins with named people should be narrated about **those people**,
not from cosmology. Left to "narrate from the big bang," a small model
(gemma3:12b) ignores the `--system` premise and writes a generic
planet-evolution story — so a Rick & Morty world came up as a Kryll/Xylos
cosmogenesis instead of stories about Rick and Morty. The fix is a
**character-driven chronicle prompt** (`_GENESIS_SYSTEM_CAST`) used whenever the
cast is already seeded: it forbids cosmology/prehistory and leads with the named
characters — their schemes, dialogue, conflicts, what goes sideways — starting
in the middle of their lives. The chapter generator threads a cast block into
the prompt so the model writes *about those people*. The SEED path (empty cast,
world names its own ensemble "from the big bang") is untouched. **A small model
given "start from the big bang" will ignore your premise and narrate cosmology;
lead with the named cast and it writes about them.**

### The explorable 3D viewer

`--viewer` serves `membrane/viewer.html` (a Minecraft-styled Three.js cosmos,
self-contained — Three.js is **vendored locally** so the page never depends on a
CDN) and `/state` (a JSON snapshot read in-process from the live field).

- **Cosmos** — stars = exemplars, PCA-projected to 3D (green safe / red
  demoted); drag to orbit, scroll to zoom. Cast marks show each character's
  region; the POV's pin moves as you think.
- **Menu (Esc / ⚙)** — all world controls: ceiling, feed-rate, CPU target,
  PLAY/PAUSE (the clock), time-skip, the legend, the activity log, the cast
  roster, the WORLD menu.
- **Chat (the main feature)** — your words render as the POV's thoughts; the
  reply is narrator prose. The sim annotation is how "right" the recalled memory
  feels.
- **Date/time/cycle HUD** — `Day N · HH:00 · <phase> · cycle C`, derived from
  `consolidation.cycles` (2h/cycle, 12 cycles/day). PAUSE freezes the clock; PLAY
  moves it. (Cycle resets on proxy restart; the field/cells persist.)
- **Neighborhood panel (`/neighbors`, press H)** — the exploration instrument.
  `GET /neighbors?q=<text>|idx=<n>` returns the top-8 nearest exemplars; after
  each chat send the panel renders the top-8 as clickable rows — click a row →
  re-render with *that* memory's neighbours → walk the field's association graph
  instead of getting one answer.

### Save / load / new world (viewer's WORLD menu)

**SAVE** copies the current field + cast + audit to a named slot under
`worlds/<name>/`; the **LOAD** list (`GET /worlds`) swaps the live store + cast
in from a saved world under `feed_lock` (so it can't race the consolidator) and
resets the rolling chronicle; **NEW** empties field + cast + audit to a blank
slate. The endpoints are `POST /world {action:"save"|"load"|"new", name}`; the
slot name is sanitized (no path traversal). A loaded world is persisted to the
live paths, so a restart keeps it.

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
  richer or just bigger. Surfaced in the viewer and `review.py`.
- **adaptive temperature** (`--variety-temp`, default off) is the field
  *spending* its requisite variety: `sim_min` eases toward `--sim-min-floor`
  (0.70, the validated safe cutoff) as `free_energy` grows. "Only **safe**
  variety absorbs variety" — a junk field pays the disorder tax, keeps
  `free_energy` low, and stays conservative.
- **the veil** (`--nudge-sigma`, default 0 = identity) is the per-instance
  perturbation of the incoming situation embedding: Gaussian noise added then
  renormalized, applied to everything that transits this membrane. The
  forwarded prompt text is **never** garbled; only the field's representation is
  veiled. A low-variety workload can't grow a varied field on its own; the nudge
  injects the variety it lacks.
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
and it is still in the code as the substrate the world is built on. It is
recorded in detail in `../cortex-experiment/membrane.md` (the cross-project
findings) and `docs/math-findings.md` (the three experiments + the
self-correcting conclusion). In brief:

- **The confidence gate** (cosine-primary) — nearest-similarity + a
  reversibility guard: confident+safe → act (replay); confident+risky →
  escalate-with-hint; not-sure → escalate. Bootstrap is conservative (`sim_min`
  starts 0.85, loosens once the audit proves safe).
- **The training loop** — passive self-train on **re-asks** (asking the same
  thing again right after an absorb means the replay was wrong). A re-ask is a
  **vote, not a verdict**: each exemplar carries `reask_count` (the
  environment's "not useful") and `contest_count` (the model's "correct anyway");
  `safe = (reask_count − contest_count) < demote_threshold`. The raw store is
  permanent; only the derived `safe` changes. The **anti-collapse rule**: the
  model's first contest restores, a later contest is record-only — the
  boundary cannot collapse to the model side. On any judge error/timeout →
  ACCEPT (degrades toward the environment, the safe direction).
- **The judge** — opt-in (`--no-judge` by default in world mode; `--judge
  --judge-model ...` to enable). A fresh model, independent of the responder,
  adjudicates each demotion: CONTEST (restore) or ACCEPT (let it stand).
- **The Anthropic adapter** — it can also front **Anthropic** (`/v1/messages`)
  so **Claude Code** drives the field, in **observer-only** mode (never replay a
  coding answer; always forward + learn + label; the gate's would-be absorb is
  audited as `would_absorb`). Ambient quality signals (tool-error,
  re-ask-after-escalate) make the judge fire from *what happened*, not a button.
  Set `ANTHROPIC_BASE_URL=http://127.0.0.1:11435` and run `./run.sh anthropic`.
  **The `ANTHROPIC_API_KEY` is passthrough only**: read from the client's
  `x-api-key` header per request, threaded to the responder, forwarded to
  `api.anthropic.com`, and **never stored on the instance, written to the field
  or audit, or printed**. The judge stays Ollama on `localhost:11434` even in
  `--anthropic` mode.
- **Measuring variety** — `effective_rank` (entropy of the covariance
  eigenvalue spectrum), `spread` (mean pairwise cosine distance), and `novelty`
  (the query's off-subspace residual). The settled conclusion: **cosine is the
  primary gate; the null-space novelty is a narrow low-rank specialist**, not a
  general gate — it earns a role only on genuinely low-rank fields
  (`effective_rank / N < 0.5`). On the high-rank situation fields the proposals
  would actually build, cosine carries the decision. See `docs/math-findings.md`
  and `../cortex-experiment/membrane.md` §2.

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
