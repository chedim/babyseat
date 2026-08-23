# membrane

A **field-first, self-authoring world** — a local proxy that grows a world on its
own clock, where the **field is the default actor** and the **LLM is a gardener**
(writes the chronicle in rare, cached chapters) and an *escalation*. You **enter
the world as a character**: your messages render as that character's thoughts, the
narrator speaks from the scenes that character *lived*, and your thoughts steer
the character's state directly. It is an explorable memory field you **probe by
association, walk as a graph, steer by pruning, and inhabit as a person** — not a
chatbot, not a game, not a search box.

This is a **Python lab**, not a production system. It is a sibling of
`rosetta-check` (the Java linter) and `cortex-experiment` (the proposals/docs).

> **Honest note on the divergence.** membrane *started* as something else: a
> "runtime memory and decision substrate for an agent (Claude)" — a confidence
> gate (absorb a cached action vs escalate to the LLM) calibrated by a
> "measure variety" math (effective rank, null-space novelty). That phase is
> built, settled, and recorded below under **[The substrate it grew from](#the-substrate-it-grew-from)**.
> The gate, the field, and the variety math are still the load-bearing substrate —
> but the project grew *into a world you inhabit*, and the variety math became the
> **thermodynamics of the world's growth** (variety = the world's life;
> `free_energy` = its safe-weighted vitality; "only variety absorbs variety" is
> Ashby's Law with the control-theory telos inverted). The world is the
> realization; the gate was the scaffolding. This README leads with what membrane
> *is*; the original vision is preserved honestly further down.

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

- `--system` is the **world premise** — the constant ground the LLM narrates from.
- `--protagonist "Name — role — voice"` writes **you** a character to be (admits
  it, sets it POV). `--character "Name — role — voice"` (repeatable) seeds the
  **ensemble** around the protagonist without making them POV.
- `--self-author --no-judge` takes the **LLM out of the live loop**: the cell
  speaks for itself from field memory (no model call per turn); the LLM only writes
  the chronicle (the gardener). Warm-up is the one other place the LLM is used.
- `--consolidate --feed-rate 4 --cpu-target 0.3` **arms the world's own clock** so
  it self-feeds and digests on its own time. (Without it the world only grows when
  you STEP/feed it.)
- You **are Rick**: your words render as Rick's thoughts, the narrator speaks from
  Rick's lived scenes, Morty is the co-lead, and the world is *about them*. The
  premise is narrated, not the cosmology — see [The character-driven chronicle](#the-character-driven-chronicle).

> **A fresh world needs seeding.** With the cast pre-seeded (`--protagonist`/
> `--character`) the field starts empty and grows from **feeds** (the consolidator,
  or the viewer's STEP/auto-flood). Until it has a few scenes the chat has nothing
  to recall — so arm the clock (`--consolidate --feed-rate 4`) and let it run, or
  press STEP a few times, before you expect replies. The chat replies are
> **recited field memory**, not model output.

---

## What it is — the architecture

### Field-first — the LLM is a gardener, not the actor

The expensive resource is **LLM tokens**, not the embedding (local Ollama,
`mxbai-embed-large`, $0 marginal). So the **field acts by default**; the LLM is the
escalation that fires only when the field is not-sure or the action is risky. In
the world mode that's the lived experience, this goes further:

`--self-author` (the running mode) takes the LLM out of the live loop entirely.
Every turn — act *and* escalate — is **the cell speaking from its own memory**, no
model call, nothing learned (no teacher). The field is coupled to the *user*, not
the model: your re-ask is the only channel back in, and it demotes the cell that
spoke, so the cells respond to you. The LLM keeps exactly two jobs:

1. **Gardener** — write the chronicle in big cached chapters (one call → ~8 scenes,
   rarely; see [the genesis cache](#the-worlds-own-clock--consolidation)). It grows
   the *content*; the field supplies the *structure* (which memories associate,
   what survives your pruning).
2. **Escalation / seed** — adjudicate demotions (the judge, opt-in; default off in
   `--no-judge`) and the one-time seed.

The **SPEAK organ** (`runtime.Membrane.generate`, selected by `--speak`):

- **`recall` (default, cohesive)** — the cell RECITES its most relevant lived scene:
  the nearest stored action to the query (kNN). The field is grown from cohesive
  LLM-written chronicle passages (~50–80 words), so the nearest neighbour IS a
  cohesive, on-topic story. Legacy telegraphic fragments (`< _COHESIVE_MIN_WORDS`:
  early stub/warm-up junk) are SKIPPED — they were the "gibberish".
- **`ngram` (opt-in)** — a fresh stochastic draw from a word-trigram trained on the
  field's own stored actions, conditioned on the nearest memory's lead. More
  "generative" but incoherent past a few tokens; an escape hatch.

Determinism holds in both modes (a query maps to a fixed nearest passage / a
query-seeded RNG), so a re-ask produces the SAME output and the re-ask detector
demotes the seed — the green→red→green self-correction arc inherits.

### The world's own clock — consolidation (`membrane/consolidate.py`)

A daemon thread (the `Consolidator`) runs the world independently of any viewer.
When armed (the viewer's PLAY, or `--consolidate` at boot), each cycle has two
phases:

- **FEED** — the world feeds itself `feed_rate` times from the **genesis cache**.
  Each feed is a cached chronicle scene → a cheap local embed + absorb, *not* a
  model call. `feed_rate` is a **continuous** float (0–32/cycle); the fractional
  tail resolves probabilistically. This is the dial that "controls the models":
  rate > 0 makes real embed calls every cycle; the model itself is called only when
  the cache runs low.
- **DIGEST** — CPU consolidation, no model, no field mutation: refresh the global
  `thermo_state` (one SVD), sweep a `budget` of cells recomputing each one's
  `novelty` (one SVD per cell), and rebuild the `FieldNGram` when the field grew.

The `budget` (cells swept/cycle) is ramped by a **closed loop** targeting
`cpu_target` — the fraction of each cycle's wall time spent digesting. So the CPU
slider truly ramps the consolidation speed against a real duty fraction.
`consolidation_stats` (cycles / cpu_actual / cpu_target / swept / budget /
mean_novelty / eff_rank / free_energy / fed / cache / stories) is echoed on
`/state` and the viewer.

**Genesis cache — one LLM call feeds the world ~8×.** The chapter generator asks
the LLM *rarely* to narrate the chronicle as a batch of `--CHAPTER`=8 substantial
scenes separated by a `---` line; the scenes are cached on `srv.story_cache`. Each
FEED pops one cached scene (cheap `/api/embed` + absorb); the cache refills with
one model call when it drops below `_LOW_WATER`=3. The chronicle continues from
`srv.story_history` (the last scenes) for a cohesive running narrative.

**No-nudge raw-embed feed; feed-rate = the entropy dial.** `runtime.Membrane.feed`
absorbs the **raw** story embedding (no `_nudge`, no `_limit_rank` ceiling-massage):
growth is **rate-controlled** (the feed-rate dial + the cache), not variety-capped.
`entropy_ceiling`/cap is now **inert for growth** (kept for the viewer's variety
display); only the dedup/vague guard limits it. The old entropy/temperature slider
is repurposed: **FEED RATE** (0–32/cycle). The story `temperature` is fixed at 0.7
server-side for a cohesive chronicle.

### The character ledger — characters are walking regions of the field

The world grows a bigger memory, but it has no persistent *entities* — no people,
no state, no causality — without a **character spine**: the chronicle's scenes tag
to *characters*, and each character carries a persistent `state_vec` — an EMA of the
scenes tagged to it, the tip of its walk through the field.

A character is **not a row in a table; it is a walking region of the field.** The
scenes are real exemplars (an `Exemplar.tags: list[int]` records which characters a
scene belongs to); `state_vec` is the EMA of a character's tagged scenes. State
lives in the field — the ledger (`membrane/cast.py`) only makes the cast
*addressable* by name and tracks each character's rolling state.

- **Geometric tagging** — a scene is tagged to every character whose `state_vec`
  it sits close to (cosine ≥ `admit_threshold` 0.40). Each tagged character's
  `state_vec` EMA-walks toward the scene (`decay` 0.6). There is **no
  auto-admit**: a scene far from the whole cast is left untagged, so the cast
  stays short (capped at 12; admission is seed-or-manual only).
- **Genesis seed** — the *first* chronicle chapter (when the cast is empty) emits a
  `cast:` block naming the ensemble (3–6 characters). One batched embed admits
  each, then a one-time `retro_tag` colors the *entire* existing field by
  character immediately. Continuation chapters tag geometrically thereafter.
- **The roster (Esc/⚙ menu)** — an RPG-style cast list: each row shows the name,
  role, `scene_count · last_cycle`, and a colour dot; **Retire** removes a
  character (its stars revert to neutral — its id is stripped from every
  exemplar's `tags`) and **Pin** protects one from accidental retire. Click a
  row → the camera flies to that character's region of the cosmos. `POST /cast`
  (`{action: retire|pin|admit|pov, id, name?}`) is the menu's endpoint.
- **Cast marks on the map** — each character's projected `state_vec` is marked and
  labelled in the world map; stars are tinted by their `tags[0]` from a 12-colour
  palette (demoted tagged stars dim). The cast persists in a sibling `-cast.json`
  file; old field files load cleanly (legacy exemplars default to `tags: []`).

### The narrator — a voiced POV character (you are a character in the book)

The spine named the cast, but the chat still read "you (green) talk to the cell
(white)" — no one was *in* the world. The narrator makes the chat a **novel**: you
*are* a character (a protagonist with a defined voice), your typed messages render
as that character's **thoughts**, and the narration speaks *from that person* — in
`--self-author` mode by reciting the scenes that character **lived** (the field
memory tagged to them).

- **A character with a voice** — `Character` carries `role` and `voice` (how they
  speak/think, in a phrase). The genesis SEED authors `Name — role — voice` per
  character and designates a **protagonist** (the reader's character) with a
  leading `★`. You can also inject one with `--protagonist "Name — role — voice"`
  (admits at boot if absent, sets POV, retro-tags the existing field so the
  protagonist colors it at once).
- **The narrator speaks from that person** — `generate()` (the self-author SPEAK
  organ) restricts recall to the POV character's **tagged** exemplars when a POV
  is set, so the recited scene is one the protagonist *lived* — the narration comes
  from their region of the field, which *is* their voice. It falls back to the
  global nearest when the protagonist has fewer than three tagged scenes (they
  haven't lived enough yet). On the LLM-escalate path the system prompt is
  reframed: "you are the narrator; the reader IS {name}; their messages are their
  thoughts; narrate in third person in {name}'s voice."
- **The chat reads as a novel** (viewer) — when a POV is set, your messages render
  as the protagonist's **thoughts** (italic, indented, `❝ name · …`); the reply
  renders as **narrator prose** (a left border, no attribution). The chat header
  shows the protagonist's name; the input prompt becomes *what does {name}
  think?*. The cast roster gains a **POV** button per row (POST `/cast
  {action:"pov",id}`) and a `★ POV` badge — click it to switch whose eyes the world
  is seen through. No POV → the chat stays green/white (unchanged).
- **Persistence** — `pov_id` rides in the `-cast.json` wrapper (`{pov_id, chars}`);
  old bare-list cast files load with `pov_id=None` and `role`/`voice=""`
  (backward-compatible).

### The I as ground — the POV state-walk

The narrator says "you ARE the character" — but the user's thoughts must actually
shape the character, or the **gardener's** scenes define it, not the user. The
I-grounding closes that gap. When a POV is set, each of the user's chat thoughts
**unconditionally** EMA-walks the POV's `state_vec` toward it (`Cast.walk_pov`,
wired into `gate_turn` — the one mode-independent point every message passes
through), bypassing the cosine `admit_threshold` that gates every other scene. The
user IS the POV character, so their thought moves the character *by fiat*, not by
similarity. **The field follows the I; it doesn't check it.**

No exemplar is appended (no recall loop, no echo of the user's own words), no
model call — one cheap EMA update per thought. The POV's region tracks where the
user steers; the gardener's future scenes accrue there; the narrator speaks from
there; the viewer's character pin moves as you think.

This is the foundation of **replacing an LLM consistency gate with the I-grounded
field** (reasoned out in `../cortex-experiment/membrane.md` §9): the model's "would
this character do this?" is a *generic, parametric* character — not your specific,
voiced one — so it would flatten your character back toward the archetype. The
gate, when it comes (for authored acts), measures acts against *your* character's
manifold — the I-shaped state — not the model's. The model stays the gardener; the
user is the ground; on the borderline the next thought resolves it, not a model
call.

### The character-driven chronicle

A world that begins with named people should be narrated about **those people**,
not from cosmology. Left to "narrate from the big bang," a small model
(gemma3:12b) ignores the `--system` premise and writes a generic
planet-evolution story (primordial soup → a world → its people) — so a Rick &
Morty world came up as a Kryll/Xylos cosmogenesis instead of stories about Rick
and Morty.

The fix: a **character-driven chronicle prompt** (`_GENESIS_SYSTEM_CAST`) used
whenever the cast is already seeded (`--protagonist`/`--character`). It forbids
cosmology/prehistory and leads with the named characters — their schemes,
dialogue, conflicts, what goes sideways — starting in the middle of their lives.
The chapter generator threads a cast block ("Rick — … — …", "Morty — … — …") into
the prompt so the model writes *about those people*. The SEED path (empty cast,
world names its own ensemble "from the big bang") is untouched — night-market
worlds still self-generate. The `--system` premise is the constant ground either
way.

### The live 3D viewer — explore the field as a graph

`--viewer` serves `membrane/viewer.html` (a Minecraft-styled Three.js cosmos) and
`/state` (a JSON snapshot read in-process from the live field). The gate path is
untouched — the page chats through the real `/api/chat`, same-origin.

- **Cosmos** — stars = exemplars, PCA-projected to 3D (green safe / red demoted);
  drag to orbit, scroll to zoom. The field's geometry rendered as space. Cast
  marks show each character's region; the POV's pin moves as you think.
- **Menu (Esc / ⚙)** — all world controls: ceiling, feed-rate, CPU target,
  PLAY/PAUSE (the clock), time-skip, the legend, the activity log (the verdict
  ticker), the cast roster, and the WORLD menu (save / load / new).
- **Chat (the main feature)** — your words render as the POV's thoughts; the
  reply is narrator prose. The sim annotation is the honest version of how
  "right" the recalled memory feels.
- **Date/time/cycle HUD** — `Day N · HH:00 · <phase> · cycle C`, derived from
  `consolidation.cycles` (2h/cycle, 12 cycles/day). A paused world freezes the
  clock; PLAY moves it. (Cycle resets on proxy restart; the field/cells persist.)
- **Neighborhood panel (`/neighbors`, press H)** — the exploration instrument.
  `GET /neighbors?q=<text>|idx=<n>` returns the top-8 nearest exemplars; after each
  chat send the panel renders the top-8 as clickable rows — click a row → re-render
  with *that* memory's neighbours → walk the field's association graph instead of
  getting one answer.

### Save / load / new world (viewer's WORLD menu)

- **SAVE** copies the current field + cast + audit to a named slot under
  `worlds/<name>/`; the **LOAD** list (fetched from `GET /worlds`) swaps the live
  store + cast in from a saved world under `feed_lock` (so it can't race the
  consolidator) and resets the rolling chronicle; **NEW** empties the field + cast
  + audit to a blank slate. The endpoints are `POST /world {action:"save"|"load"|"new",
  name}`; the slot name is sanitized (no path traversal). `--worlds-dir` (default
  `worlds`) sets the directory. A loaded world is persisted to the live paths, so
  a restart keeps it.

---

## The thermodynamic principle — "only variety absorbs variety"

This is the project's principle, in plain words. It is Ashby's Law of Requisite
Variety with the control-theory skin peeled off. Control theory's telos is
*anti-variety*: the regulator exists to **collapse** the disturbance's variety down
to a controlled, low-variety output ("destroy variety") — variety is the adversary
to be neutralized into order. The membrane inverts the ethics: variety is not the
threat to be contained, it is the **life to be grown**. `free_energy` (safe
variety) is the field's vitality — a field with no safe variety is inert (it
escalates everything, acts on nothing: dead). Same law, opposite telos: control
theory says "grow enough control variety to *keep the output controlled*"; the
membrane says "grow enough field variety to *absorb more of the world*."

In the code:

- **`effective_rank`** = the field's requisite variety, *measured* not assumed. A
  query off the experienced manifold (high `novelty`) can't be absorbed → escalate.
  Only a field with variety *in that direction* can absorb a query in it.
- **`free_energy`** = `free_energy(store)` — safe-weighted variety minus a disorder
  tax: `(safe fraction)·effective_rank − ¼·(unsafe fraction)·rank`. The *one
  number* that says whether the field is getting richer or just bigger. Surfaced
  in the viewer and `review.py`.
- **adaptive temperature** (`--variety-temp`, default off) is the field *spending*
  its requisite variety: `sim_min` eases toward `--sim-min-floor` (0.70, the
  validated safe cutoff) as `free_energy` grows. "Only **safe** variety absorbs
  variety" — a junk field (variety but all demoted) pays the disorder tax, keeps
  `free_energy` low, and stays conservative.
- **the veil** (`--nudge-sigma`, default 0 = identity) is the per-instance
  perturbation of the incoming situation embedding: Gaussian noise added then
  renormalized, applied to **everything that transits this membrane** — both the
  gate's matching query and the exemplar vec that gets stored. The forwarded prompt
  text is **never** garbled; only the field's representation is veiled. A
  low-variety workload can't grow a varied field on its own (dedup collapses
  paraphrases to one point); the nudge *injects* the variety it lacks.
- **multi-veil** — "run multiple of them" is **decentralized**, not orchestrated.
  No central input, no live router (a central oracle is a rank-1 field — one
  perspective — and rank-1 cannot absorb variety). Each membrane is **autonomous**
  — own field, own audit, own nudge seed, own temperature — pulling the same public
  workload. `--tag <name>` defaults the field/audit paths to
  `membrane-field-<tag>.json` / `membrane-audit-<tag>.jsonl`. Comparison is
  **offline**, reading each membrane's saved audit afterward.

```bash
# launch several autonomous membranes at different temperatures/veils, then
# compare their thermodynamic trajectories offline (each wrote its own audit):
PYTHONPATH=. .venv/bin/python experiments/multi_veil.py
```

The experiment runs four autonomous membranes over the same low-intrinsic-variety
workload and reads each one's saved audit offline. The `free_energy` column is the
principle in numbers: the **static** membrane is stuck at the workload's intrinsic
variety with `free_energy` going **negative** (dead); the **veiled** ones grow
`effective_rank` past the base count and stay safe (`free_energy` ~ +4 — alive).

---

## The substrate it grew from

membrane's first job was an open question the design left hanging:

> **How do we measure "variety" for the Membrane process?**

The answer is settled (cosine is the primary gate; the null-space novelty is a
narrow low-rank specialist — see `docs/math-findings.md` and
`../cortex-experiment/membrane.md` §2). That math is still here, as the
thermodynamics above. What's below is the **gate / training / judge** machinery
that was the original vision — still in the code, still useful, and the foundation
the world is built on.

### The confidence gate — absorb / escalate

```
task arrives
   |
   v
embed the query (1 local embedding) --- cosine scan over the pre-built field
   |
   v
confidence gate:  sure?  AND  safe?
   |                    |
   yes + safe -----------> FIELD ACTS (replays the recalled action)
   yes + risky ----------> ESCALATE TO LLM, with the recalled action as a hint
   not-sure --------------> ESCALATE TO LLM
   |
   v
every field-taken action is logged -> audited -> the audit IS the next training round
```

`not-sure` = ANY of: **low nearest-similarity**; **high neighbor-disagreement**
(the top-k voted for different actions); **high novelty-vs-variety** (the query is
off the experienced manifold — the variety signal). `sure` and `safe` are separate
axes (confidence is not safety): confident + safe/idempotent → act; confident +
risky (commit, push, delete, send) → escalate-with-hint (recall without trigger);
not-sure → escalate. The bootstrap is conservative: `sim_min` starts at 0.85
(near-exact) and loosens once the audit proves replays safe.

### The training loop — passive self-train on re-asks

The decision loop (absorb/escalate) is built. The **training** loop is how the
outcome — *was an absorb correct?* — flows back. The key property: **it is
passive.** You use the chat normally; Membrane learns from how you naturally use
it. You never "teach" it.

The strongest outcome signal is a **re-ask**: you ask the same thing again right
after the field *absorbed* (replayed) it — meaning the replay did not satisfy, the
absorb was wrong. Membrane sees it because every request flows through it; no
notification, no model call. (A same-Q repeat after an *escalate* is the legitimate
first absorb, not a re-ask.)

**A re-ask is a vote, not a verdict.** It does not treat the environment as
infallible. Each exemplar carries two evidence counters, and `safe` is *derived*:

```
safe = (reask_count - contest_count) < demote_threshold
```

- **`reask_count`** — the environment's vote: "this replay was not *useful*."
- **`contest_count`** — the model's vote: "my answer was *correct* anyway; that
  re-ask was a misunderstanding."

Each side checks the other; neither has a monopoly; both keep a veto. Demotion
*accumulates* and is *reversible* (a contest cancels a re-ask's vote and may
restore `safe`). A re-ask also forces *that turn* to escalate-with-hint — never
replay the answer just rejected. The raw store stays permanent; only the evidence
counts (and the derived `safe`) change — nothing is deleted or rewritten.

**The anti-collapse rule (the model supplements, never substitutes):** the model's
**first** contest RESTORES (one veto — it can cancel one demotion); a **later**
contest is **record-only** (the environment re-asserted by re-asking again =
irreducible disagreement → the demotion stands, the model's claim is logged). So a
reflexively-defensive judge gets ONE veto per exemplar, and the environment's
repeated re-ask wins the action — the boundary cannot collapse to the model side.
On any judge error/timeout → ACCEPT (boundary degrades toward the environment,
the safe one-sided direction).

The judge is **opt-in** (default `StubJudge` never contests — zero behavior
change, zero extra calls; `--no-judge` in the world launch). Pass
`--judge --judge-model ...` to enable it. On error/timeout the verdict is ACCEPT.

```bash
# review what Membrane learned (the in-window training-loop surface):
PYTHONPATH=. .venv/bin/python -m membrane.review --audit membrane-audit.jsonl --field membrane-field.json
# live ncurses view of the same field + audit (reads files only; second terminal):
PYTHONPATH=. .venv/bin/python -m membrane.tui --field membrane-field.json --audit membrane-audit.jsonl
```

### The Anthropic adapter — Claude Code, observer-only

Membrane can also sit in front of **Anthropic** (`/v1/messages`) instead of
Ollama, so **Claude Code** drives the field:

```
Claude Code --> Membrane (proxy, observer-only) --> api.anthropic.com
```

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:11435
export ANTHROPIC_API_KEY=sk-ant-...        # a real key; subscription OAuth won't route through a custom base URL
./run.sh anthropic                         # TUI + proxy, --anthropic + observer implied
```

**Observer-only: never replay, always forward + learn + label.** Claude Code turns
are huge, tool-using context windows; replaying a stored answer for that is unsafe
(the right action depends on the *full transcript*). So in `--anthropic`/`--observer`
mode the delivery layer **never replays**: every turn is forwarded and learned, and
the gate's would-be absorb is audited as `would_absorb=true` (observability — see
what *would* have cached, for a future `--absorb` switch). The SSE stream is teed
to the client verbatim while `text_delta` text is accumulated into the field.

**Ambient quality signals (the "no dislike button" philosophy).** With no replays,
two ambient signals (gated to `--observer`) make the judge fire from *what
happened*, not a button the user presses: **tool-error** (a `tool_result` with
`is_error: true` means a tool the model invoked failed in the world → demote the
action that led to it) and **re-ask-after-escalate** (a near-duplicate of a recently
*escalated* turn → the forwarded answer didn't satisfy → demote it). Both reuse the
existing demote → judge path.

**Information-gated learning.** Two learn-time guards stop the field filling with
junk: the **vague filter** (default on) skips low-information situations (`yes`/
`ok`/`beep`-class); the **variety gate** (`--variety-gate`, default off) only
appends an exemplar if it grows `effective_rank` (2× SVD per learn, so off by
default — the cheap vague+dedup cover most cases).

**API-key discipline.** The `ANTHROPIC_API_KEY` is **passthrough only**: read from
the client's `x-api-key` header per request, threaded to the responder, forwarded
to `api.anthropic.com`, and **never stored on the instance, written to the field or
audit, or printed**. The judge stays Ollama on `localhost:11434` even in
`--anthropic` mode. Verified end-to-end by a fake-Anthropic SSE test suite.

### Measuring variety — the math

The variety measures (in `membrane/variety.py`):

- **`effective_rank(store)`** — the entropy of the covariance eigenvalue spectrum.
  Low → exemplars cluster along a few axes → low variety. This generalizes the
  linter's fixed-`rank` lesson: instead of picking a magic `k`, we measure the
  *effective* dimensionality the field has actually seen.
- **`spread(store)`** — mean pairwise cosine distance. A crude, cheap variety
  proxy.
- **`novelty(query, store)`** — the query's off-subspace residual, normalized by
  the store's own null-space width. High → the query is in a direction the field
  has no variety in → escalate. Earns a role **only on genuinely low-rank fields**;
  on the high-rank fields the proposals would build, cosine carries the decision
  and the null-space novelty is a conditional veto (`effective_rank / N < 0.5`).

The one-SVD thermodynamic record is `thermo_state(store)` — `{eff_rank,
spectrum_entropy, free_energy, rank_ratio, safe_frac, n}` from a single SVD. The
gate pays for that SVD every turn (for the novelty on/off boolean), so `gate_turn`
records `thermo_state` once and hands `eff_rank` into `gate.decide`, avoiding a
second eigensolve. Every audited decision carries the thermodynamic state it was
made under.

---

## The math findings

`docs/math-findings.md` is the full narrative of the three experiments (real
embeddings → rank-growth → consolidation) and the self-correcting conclusion. See
also the experiment files in `experiments/`, and `../cortex-experiment/membrane.md`
for the cross-project fold (what the lab measured that corrects the Cortex
proposals' load-bearing assumptions).

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