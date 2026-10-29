# The entity field

Membrane remembers per **entity**, not per exchange. Every file, symbol,
command, port, model, URL, phrase and word the project talks about is an
entity; each has a state vector that the sentences mentioning it move. What
the model is shown is rendered from those entities: the sentences that shaped
them (verbatim), what tools observed about them, what the code says, and
where the evidence contradicts a claim.

This replaces the exemplar field (one vector per situation/response pair).
Experiments e17–e25 showed why; the evidence is at the end.

## Two streams, one log

Everything captured becomes an **event** in the `events` collection, the
source of truth:

| stream | what | written by |
|---|---|---|
| `discourse` | the user's messages, the model's prose, its reasoning, notes (`learn`), ingested docs (`doc`) | `capture.on_turn`, `learn`, `ingest` |
| `observation` | one event per tool call: tool, parsed args, result head (6 KB) and tail (2 KB), outcome class, the message it served | `capture.on_calls` / `on_tool_result` |
| `code` | one event per definition: `qual(sig) in file: <docstring head>` | the codegraph refresh |
| `distilled` | the old compact notes: a source of claims, never of state | the backfill only |

`membrane/streams.py` splits a turn (or any of the six historical audit
shapes) into events. Discourse never contains tool syntax; that is pinned
as a test. Events are content-keyed, so a re-sent turn is a no-op, and a
tool's result upgrades its call-only observation through the same call id.
Each event has a microsecond `seq` (the order) and a short per-origin
number `n`, the `#N` the blocks cite and `forget #N` removes.

Turns arrive from every surface through the same door: the TUI seat, the
native `membrane serve` room-facing server (opencode replaced; its turn
loop calls the proxy's own `/v1` on loopback, so gate, injection, capture
and banner are never bypassed), and the desktop seat. There is no second
capture path.

Each captured event also carries its **conversation** (`conv`, #429): the
client's own session id when it sends one (`sid:<id>`: the serve's loopback
turn, opencode's `x-opencode-session-id` / `x-session-affinity` /
`X-Session-Id`, Claude Code's `X-Claude-Code-Session-Id`), else a hash of
the transcript's first user message (`head:<hash>`), which every turn of one
chat repeats. OpenAI's `user` field names a person, not a chat, and is not
used. A request with neither carries no `conv`; its events keep only the
seat's idle-gap `session` (a new one after 30 idle minutes), which alone
would merge two chats running at once. A discourse event's content key
includes the conversation, so the same "yes" said in two chats is two events.

## Entities

`membrane/eids.py` gives every mention a canonical id `kind:norm`:
`f:membrane/router.py` (repo-relative), `s:membrane.router.Router.pick`
(module-qualified), `c:git`, `port:11500`, `m:glm-4.6`, `u:…`, `tool:edit`,
`p:replay guard`, `w:gateway` (plural-folded). Identifiers win over phrases,
and phrases over words.

Each entity is a document in `entity_states`:

- **seed**: the bare term's embedding (its everyday meaning), cached in `terms`;
- **state** `← unit(state + 0.5·(x − state))` per mention sentence `x` (the
  *local* sentence, not the whole message; e18: span tracking distinguishes
  entities 68% vs 59% for whole messages);
- **mean** and **rsum** (the sum of residuals against the stream's
  background), from which **coherence** `(|rsum|² − n)/(n(n−1))`, the mean
  pairwise cosine of its residuals: do its sentences agree on a direction?
  A word is *coherent* above the null μ+3σ (random sentence sets);
- **mass** (`accr`): mentions whose alignment with the running mean clears
  0.15, i.e. accumulated agreeing evidence;
- **specialization** (how far its use drifted from the seed) and
  **activity** (did its meaning keep moving);
- tier: words and phrases wait in a **nursery** until 3 distinct sentences
  (their buffered mentions are then replayed); identifiers are live at once.
  Boilerplate sentences (seen more than 8 times) move nothing.

Every mention is also stored in `entity_deltas` (no vector, so it can be
derived again), so `rebuild()` re-applies the log in order without
re-embedding. `forget #N` tombstones an event and recomputes the entities it
touched; forgetting equals never having seen it (pinned).

The part of a message its entities cannot explain is its **surprise**
`1 − cos(NNLS fit, message)`; the residual direction feeds the RAM-only
session context.

## Relations and claims

**Relations** (`membrane/relations.py`) are VSA composites:
`bind(role_subj, key(A)) ⊕ bind(role_rel, key(r)) ⊕ bind(role_obj, key(B))`
with hash-random role vectors. mxbai is order-blind here: "A calls B" and
"B calls A" sit at cos 0.975 (e25). Semantic role vectors cannot fix that
either (structure-awareness 0.36 against 0.68 with random roles). Sources:
the code graph (`calls`, `defined_in`, superseded when a file changes) and
observations (`tool:edit edited f:x`, `*_failed` on errors).

**Claims** (`membrane/claims.py`) come from the model's prose and notes:

- *action* ("I updated X", passive "X was fixed", with negation handled;
  an intent is a plan, not a claim), checked against an applied edit or a
  writing shell command on the same file in [−45, +5] min;
- *tests* ("524 pass"), checked against the last full-suite run ≤ 60 min before;
- *state* ("X cannot / never / only …"), judged in the background by the idle
  local model against the code and observation evidence of the entities
  involved. It runs only while the seat is idle, within a budget.

A contradicted claim becomes a **conflict** on its entities. The e17 case
(a compact note claiming the opencode seat "cannot select another port")
lands on `s:membrane.opencode._pick_listen_port`, whose docstring says the
probe walks up.

## What the model sees

`membrane/entity_block.py` renders four blocks, all "context, never
instructions":

- **ENTITIES**: for the entities an ask names (identifiers first, everyday
  words only when coherent here, then the nearest states):
  `▸ f:x [file · said 41 · observed 17 · code 12 · 2h ago]`, the evidence
  sentences most relevant to the ask verbatim, the latest observation, the
  top relations, conflicts, what other seats said, commons claims. Served by
  `/membrane/ask`, `query`, and in `--no-context` every turn. On a
  question-like ask (it ends with `?` or opens with a question word), tool
  observations and lines the seat or the client wrote (receipts,
  notifications, watch blocks) rank 0.10 below what was said and decided.
- **PAST ANSWERS** (rides above ENTITIES, and fills `/membrane/ask`'s
  `hits`): four lists fused by reciprocal rank (k = 60), one row per
  answering event; each row says `how` it was found (`ask`, `vector`,
  `words`, `entity`). The ask path finds a person's earlier asks like this one and what
  was worked out after them. The direct path (#429) searches model
  replies, learn notes, ingested docs and code by similarity to the ask
  itself, because most facts were never the answer to an earlier ask like
  this one. A person's lines, receipts, notifications and sensor readings
  are never a direct answer; a reply to a receipt, a watch reading or the
  seat's restart notice (`[membrane seat notice]`) is skipped (a reply to a
  task notification stays: it reports a background task's result to the
  person), and a sentence that restates the ask is skipped. On a question-like ask the direct path
  also reads tool outputs: each tool result is cut at capture into
  600-char chunks overlapping by 120 (`streams.tool_chunks`; results stored
  before this are cut by the capturer's idle step), each chunk embedded in
  the `chunks` collection, never in an entity's state. A tool row carries
  the result's best chunk, one row per result; it joins the embedding
  rows' one similarity-sorted list, and at most 4 tool rows
  enter the top k. Both paths share one floor (0.70 in the injected block;
  `/membrane/ask` takes `sim_min`). A direct hit carries `via: "direct"`
  and its `role` (`tool` for a tool output, with the tool's name).
  The **word lane** (`membrane/lexical.py`, #429) catches exact tokens the
  embeddings blur: identifiers, env vars, issue numbers, file names. It is
  BM25 (k1 1.2, b 0.75) over the field's own sentences, in RAM, built from
  the events on a boot thread (recall runs without it until it is ready)
  and kept current by capture. Words are lower-cased, snake_case and
  dotted or dashed compounds count whole and by parts, plurals fold, and
  Lucene's 33 English stop words are dropped. A word in more than 1% of
  the sentences (at least 20) is skipped. The **entity lane** reads the
  entities the ask names, found by capture's own extractor
  (`membrane/entities.py`: files, symbols, models, urls, shell commands even
  outside backticks, home-assistant ids) or a word written as a name. Its
  pool is the events that mention them (the per-entity deltas, and the word
  index for the name's words), answering rows only, the newest 24, so one
  hot name cannot flood it. Pool events whose sentences carry the ask's
  other rare words come first, then the rest by similarity to the ask. Both answer
  under the direct path's roles. When the ask has a vector, a word row
  counts only if it matched a word written as a name (a digit or a
  separator in it, or a capital past its first letter); such a row, and
  an entity lane row, pass the floor. An ask too short for a sentence vector (a bare
  `PACE_S`, `#171`) is answered by its words alone. Word rows carry the
  `matched` words.
- **SINCE YOUR LAST MESSAGE** (normal mode): the entities whose state moved
  after the watermark of the last model turn delivered. Changes from
  *outside* the conversation (code edits, entity scripts, other sessions)
  are quoted; the conversation's own turns only count. New conflicts come
  last. No block means nothing changed.
- **THE CONVERSATION SO FAR** (`--no-context`): the session rebuilt from
  the event log, with messages verbatim (clipped) and tool calls as one-line
  facts.

The seat card explains the blocks, said / observed / code, and the trust
order: observed and code > the user > model prose.

### THIS CONVERSATION

By default (#429; `membrane/conv_banner.py`; `MEMBRANE_INJECTION=banner` restores the
old block) the per-turn state is one block about the chat the turn belongs to (its
conversation key, `conv` on events), derived from records with no model call:

| line | source |
|---|---|
| `NOW` | the seat's local date, time and time zone |
| `WORKING ON` | the person's latest earlier ask in this chat (its person-authored user events, speaker line cut) that names a focus entity, else the latest one; clipped to 160 chars |
| `ABOUT` | up to 5 focus entities: named in the person's latest message, or in at least 2 of this chat's events (an identifier no code defines, `s:?`, only when the person names it) |
| `DECIDED` | the newest 3 of: ledger entries said in this chat, goals and json-board intents fulfilled in it (`fulfilled_conv`), coproc `decide()` records made in it |
| `OPEN` | the newest 3 goals (`convs`) or json-board intents (`conv`) planted in this chat and still open |
| `STABLE` | up to 3 facts (coproc `remember()` values, ledger lines) seen in at least 3 chats over at least 2 days, never given another value, about a focus entity |
| `SINCE YOUR LAST MESSAGE` | up to 3 lines (200 chars) on focus entities moved after this chat's last delivered turn by code, scripts or other chats |
| `ALERT` | at most 2 per turn; the rest ride the next turns |
| `MORE` | the pull pointer (`state(section=...)`, `query`, `entity_read`), only when the request carries tools |

Every record line ends with its source and age: `just now` (under 5 min),
`N min ago`, `Nh ago` (under 12 h), `this morning` / `this afternoon` /
`yesterday`, `N days ago` (under 7), then the date. The whole block stays
within 900 chars (lines that do not fit are dropped, least important
first), an empty section is omitted, and a chat with nothing on record gets
no block. It is sent every turn while it has content (no 8-turn delta
gate); it replaces WHERE WE ARE, the entity diff and the open-intentions
block on that path. Raw event numbers, command counts, class proposals and
tracks stay behind `state(section=...)`.

WHERE WE ARE's tracks are entities too: `track: f:x — said N · observed M,
last 2h ago; latest: <the newest sentence that moved it>`, with boundary
alerts on the mention count, and the now-line listing the entities moved
most this session.

### The banner is recall (#668)

After the chat block, the banner carries a `RECALL` block: the best rows the
field holds for the person's **latest message** (no model call, no new lane).

1. **Query:** the message. One with no topic ("ok", "1. yes", "thanks!": fewer
   than 0.3 of its tokens are content words) keeps this chat's previous set.
2. **Candidates:** `recall_answers`' top 50 rows, from the existing lanes.
   The earlier-asks lane stays 3 wide whatever the pool (`ASK_K`): widened to
   50 it searched 1,800 sentences and took 16 of a live turn's 20 s for the
   same rows.
3. **Order:** a pointwise logistic scorer over features recall already has:
   cosine, 1/rank, BM25 and content-word overlap with the message, speaker
   (the row's event was said by a person the message names: the event's own
   speaker entity), the best similarity among neighbouring turns in the pool.
   No length feature (on a seat long rows are tool dumps). The weights live in the field, not in code:
   collection `config`, key `recall_scorer` (`recall_banner.load_weights` /
   `save_weights`). The starting weights are #664's arm 4 (pointwise logistic regression, fit
   on LoCoMo conversations 2-9, 1301 questions, over these exact features, on
   pools from the unmerged `recall-fuse` lane):
   on the held-out conversations 0-1, hit@8 goes 153 -> 169 and hit@20
   175 -> 187 (recall with #539's entity lane and #532's whole-event score;
   without them 106 -> 148). The spread lane of `recall-fuse` adds +2 at twice
   the recall time and is not merged. The night refit (below) moves them from what the model's
   own searches use.
4. **Lines:** the top 12 rows, one fact line each (the row's sentence nearest
   the message, 200 chars), skipping the current chat's own last 8 turns (the
   echo failure of #649) and collapsing near-duplicates (word overlap >= 0.8).

`recall_banner.py` holds the constants (12 / 50 / 0.3 and arm 4's weights on
#668; echo window 8, dedupe 0.8, clip 200 on the PR 669 review: all
operator-approved). The block sits outside the 900-char chat budget: at most
12 lines, about 2.8k characters (~700 tokens) at the clip, less in practice.
Cost: the warm build is ~60-90 ms on a 4,500-event field (recall's pool scan
indexes each session once and tests IN lists as sets).

### The banner learns from use (#668, step 6)

When the model **searches** (`query`, or `find` on memory) after the banner, the
banner missed or ranked too low. `recall_labels.py` keeps what is needed to learn
from it:

1. **At the search:** the banner's pool (each row's features and fact line, in the
   scorer's order) and the search result go to the field, collection
   `recall_labels`, as an open record.
2. **When the reply lands:** the pool row whose words are in BOTH the search result
   and the model's reply (at least 60% of its content words) is the positive; the
   rows the scorer ranked above it are negatives (near copies of the positive
   excluded). Found AND used, not every read. A reply that uses no pool row, or no
   reply within a day, drops the record. A positive that is not in the pool is a
   recall miss: nothing for the scorer to learn.
3. **The night pass** (`efmind.night`, step `recall`) resolves open records, deletes
   labels older than 90 days, and, once there are 20 positives and labels the last
   fit had not seen, refits: a weighted logistic fit (damped Newton) pulled toward
   the current weights by one pseudo-label, a label's weight halving every 14 days.
   The result is written to `config/recall_scorer` (with `fit_n`, `fit_ts`,
   `fit_pos`), so the seat's own use replaces the starting weights.

The constants (wait 1 day, 60% use share, 14-day half-life, 90-day keep, 20
positives, 1 pseudo-label) are in `recall_labels.py` and wait for the operator's
approval on the PR.

## The division of hands (2026-10-06)

The native serve's turn lane carries the session's **agent**, and the
agent decides which MCP tools the turn's surface carries and which
calls the executor admits. Two minds, one registry (`_lane_tools`,
membrane/server.py):

- **Operator** (agent `build` — the main chat): every hand since the
  flood gates (2026-10-08): reads (`query`, `state`, `graph`,
  `entities_related`, `entity_query`, `entity_read`, `entity_tracks`),
  memory (`learn`, `forget`), steering (`intend`, `intents`,
  `fulfill`), room (`peer_post`, `peer_seats`, `peer_read`, `junk_cut`), the watches
  (`entity_watch`, `entity_unwatch`, `field_watch`, `attention`) and the
  entity hands (`entity_add`, `entity_edit`, `entity_remove`,
  `entity_inherit`) and the method tools (`method_define`, `method_call`,
  `method_each`, `method_inspect`). The chat defines and calls a method
  itself instead of planting a goal for it.
- **Event listener** (agent `watch:<eid>` — the thinking loops): the
  operator's set **minus** `intend` (a loop takes the next step, closes
  with `fulfill`, or ends BLOCKED; it never plants new goals).
- **Phone** (agent `phone` — a call through agdroid's calld): reads,
  `learn`, `intend`, `intents`. Never the executor or entity
  writes: a misheard sentence must not change the boat; a caller's
  request becomes a visible goal.
- **Plan** (agent `plan`): the operator minus every write and close — a
  planning session reads and communicates, never plants.

Refusal texts are lane-specific: only a plan session hears "plan mode".

A cross-lane call never reaches the impls: the tool result IS the
honest refusal (a pointer to the right lane), audited
`lane-refused`. The agent rides each message (the attached TUI sends
`agent` per message POST); the turn's agent wins over the session
row's, and the pick **sticks** (the session row updates — the mode
toggle survives restarts of the conversation). Kill switch:
`MEMBRANE_LANE_GATE=0` (runtime-read) restores the flat surface. The
launch-mode stdio MCP child (an external opencode's config) keeps the
full surface — the per-call agent is unknowable there; the native
serve is the enforcement point.

Listener-owned watches deliver differently: `entity_watch(owner=
"watch:<eid>")` routes the boundary crossings into the events pool —
the listener receives its own watch's firing as its next turn's
message (the same event-input flow every loop turn rides). Operator
tracks keep the banner ALERT delivery; `entity_tracks` is owner-aware.

## Intent lane (2026-10-06)

Intentions steer the idle mind and are planted by the model: the
`intend` tool / `membrane: intend that …` frame create-or-pin a shared
Goal entity and pin it on the seat's active set; `intents` reads the
board; fulfillment is explicit (`membrane: fulfill #N`). An entity
script may plant too — its result carries `{intent: …}` and the engine
plants through the same lane (night.py's apply). The plant is an
EVENT (`intent-planted`) a thinking loop receives as its next turn
(plan or ignore) — the operator creates, the thinker receives and
executes. While the seat is paused, planting still works and is inert
by construction: every lane that would react is down too.

## The executor: code is a tree in the field (#614)

`tool:executor` (seeded at boot, `lang: sh`) is the seat's shell executor.
The model runs code with `exec(action='run', code=..., lang='sh')`
(`code_run`, `membrane/lang.py`). `bash {command, timeout (ms),
description}` is the same run under the name Claude Code and opencode
give their shell tool: the MCP list carries it, and a local model's
`bash` call resolves to `code_run` (`toolset.bash_args`):

- **The code becomes nodes.** It is parsed with the language's
  tree-sitter grammar (`tree-sitter-bash`, `tree-sitter-javascript`) into
  a tree, one entity per named syntax node (its `kind` is the grammar's
  node type), **content-addressed**:
  `node:<lang>:<hash>` over (lang, kind, text, ordered children). The
  same subtree is one entity wherever it appears (`..`, `git status`), so
  the field sees what code shares. Children are ordered edges `-arg:1->`,
  `-arg:2->` ... (relations rows; the order is in the label, so `cp a a`
  keeps both places). A node keeps its source text with a hole per
  child, so printing the tree gives back the code exactly as written.
  Code with a syntax error is refused.
- **The run is an edge** (the chat adapter's shape: a write, then its
  effect): `root -execute-> executor` is owed work; taking it is a
  compare-and-set that turns it into `-executing->` (in progress); when
  the run lands, that becomes `root -result-> result:<root>:<n>`, a
  result entity for that run (`rc, outcome, head, tail, ms, text, ts`).
  A runner that raises still ends on a result (`outcome: failed`).
  Anyone may write the execute edge (the tool, a loop, a trigger): the
  `exec` adapter branch reacts at once, and the thinker's reaper takes
  what is still owed each minute.
- **Re-running** is re-creating the execute edge
  (`exec(action='run', node=<root>)`): each run keeps its own result.
- **Collection** (`lang.collect`, every night pass): one seat option,
  `code_runs_keep` (default 30, the ⚙ options window), bounds every
  history: each root keeps its newest results, each method its newest
  bound trees (`bound` on the method), each executor its newest one-off
  trees (by their last run, or their birth). The rest are deleted with
  their results and edges, then every node no kept tree reaches. Shared
  nodes stay while any kept tree uses them. A method's own code and a
  run still owed or running are never collected.
- **Languages** are a registry (`lang.LANGS`): a parser into nodes, a
  printer out of them, and a runner. An executor names its language in
  `lang`; a root of another language is refused with a receipt. The
  sh runner is `run_command_entity`, so the `c:<head>` doc, the
  observation event and the audit line still record every run.
- **js**: its executor is the seat's `presence`: the run goes to an
  opted-in browser tab (`jsexec.run`, 15 s). The old door, the
  `presence.exec` method, is retired and removed at boot.
- **Reading**: `entity_read` on a root shows its code, state
  (`owed` / `executing` / `idle`) and runs; on the executor, its queue:
  `pending`, `running`, `done` (with receipts).
- **Without a relations store** (capture off) the same states live as
  properties (`pending_run_by`, `executing_on`, `results`); a node
  minted then gets its `arg:N` edges once the store is on.
- **Code is stored as written**: every node and each result's `text`
  keep the literal code, so a token typed into a command stays in the
  field. Pass secrets through the environment, never in the code.

Methods run their commands as code too (#614 part C). Defining a method
mints its command as a tree (`code` on the method), each `{{name}}` (or
a quoted `'{{name}}'` / `"{{name}}"`) a `param` node. A call binds the
params (each value shell-quoted once; a `{{parent...}}` feed goes in as
the parent printed it) and runs the bound code through the same
execute → executing → result edges, so every call leaves a result
entity. A method from an older seat gets its tree on its first call. The
text templates (`_fill`, `substitute_args`) are retired. A method call
straight at the executor is refused. Their composition:

- **Composition IS inheritance**: a command entity's ISA parents
  (`entity_inherit`) are its pipeline. A run runs the node's
  whole ancestry **post-order** (parents first), MEMOIZED per eid (a
  diamond's shared ancestor runs once), then the node itself — fed
  the parents' outputs through substitution: `{{parent.head}}` /
  `{{parent.tail}}` (nearest parent), `{{parent:<eid>.head}}` (named
  fan-in — multiple parents are wait-all, the child's command names
  what feeds where). A Tool **without** a command but **with** parents
  is a composition node: it runs its subtree and carries the last
  parent's residue as its exit (a pipeline header). ISA edges on
  non-command entities (Components/Goals) stay classification — never
  execution. Stop-on-failure: a failed step halts the walk (`&&`
  semantics); the child marks `failed_step`/`failed_reason`.
- **The caps**: 12 nodes per pass (a deeper ancestry reports
  `continued: True` and the background reaper finishes it next beat);
  depth 16 (the ISA walk's own MAX_CHAIN).

The composition loop: author small methods once (build, test, scan),
wire them into pipelines REUSING them (steps or ISA parents), call, and
every step's residue stays readable for the next composition. The
model-facing teaching: doctrine.md + the `entity_add`/`entity_watch`
tool descriptions.

### The durability plan (the seat's own design, delivered)

Three homes, one rule — **facts durable, states volatile, wakes
derived**:

- **The receipts** (the symmetry rule): every supersession carries a
  RECEIPT written on the edge row — `{op: done|expired|died|withdrawn, ts,
  rc, outcome, why}` — the relations journal persists it (a reload
  replays the receipts). A cancellation is as durable as the debt;
  the phantom-debt exhibit (46 live no-receipt edges surviving boot
  after boot) dies here.
- **Expiry-with-residue**: a run owed past its TTL (24h,
  `EXEC_EDGE_TTL_S`, counted from the execute edge's last write)
  expires — stamped + received — never run; the boot's book-balance
  runs it, so does every NightClock pass. Re-running the root is the
  deliberate re-queue. A run still `-executing->` past the longest a
  run may last (`RUN_TIMEOUT_MAX_S`, 600 s) died with its seat: the
  same pass ends it with an `op: died` receipt.
- **Boot-derived wakes**: the boot reads the open intents and
  re-queues an `intent-planted` event per unclaimed goal (a
  `intent_planted_ts` stamp guards the double-fire). The volatile —
  lanes, pools, loops — stays RAM; the DURABLE board re-derives them:
  plant a goal, restart, come back tomorrow — the listeners wake on
  the board's authority.
- **The claim receipts**: a listener's landing for a goal stamps the
  GOAL's doc (`landings[]`, capped 8) — the fulfill-by evidence; the
  operator fulfills on receipts, never on vibes.
- **The residue honesty**: a shell run's outcome reads the RC as the
  ground truth (a zero-exit run that *mentions* 'error' in its output
  keeps its `ok` class — the `rc=0 error` residue bug is dead).

## The gate and the replay A/B

`membrane/efgate.py` runs on every real turn. It finds the earlier user
message nearest the ask and its answer (the last model message before the
next user message). Only a person's messages count as earlier asks: job
receipts (`[membrane] job #`, `your job #`), task notifications
(`<task-notification>`), `[membrane-watch]` / `[membrane-intercept]`
blocks and the seat's restart notice (`[membrane seat notice]`) also
arrive as user turns, so each user event is tagged with its
`author` at capture, and an older event with no tag is read by the same
prefixes (#429). The same rule picks the earlier asks behind PAST ANSWERS. The answer is replayable only when all of these hold:

- the similarity is ≥ 0.92;
- the asks' specific entities agree (Jaccard ≥ 0.8);
- the other near asks got a similar answer;
- the answer is trusted and was never re-asked;
- the ask's pronoun resolved;
- **the staleness guard**: no observation, code change or conflict on the
  ask's entities is newer than the answer.

Replay is **not served** by default. Every eligible turn is logged as an
A/B pair instead: the replay next to the model's actual answer, their
similarity, and later the idle judge's verdict (`agree / partial /
disagree`). `membrane review --ab` reports the pairs. `--replay-ab P`
serves the replay on a fraction P of eligible turns.

## Storage

Couchbase scope `membrane`, nine collections: `events`, `sentences`
(content-addressed embeddings, shared by all origins), `terms` (seeds),
`entity_states`, `entity_deltas`, `relations`, `claims`, `annotations`
(replay A/B, trust), `origin_stats` (backgrounds, the coherence null, the
applier cursor and lease, the backfill cursor). Composite indexes cover the
scans the field runs; a vector index on `entity_states.state` and
`sentences.vec`, with a brute-force fallback. Writes to one entity go
through CAS; one applier per origin holds a lease.

Vectors are stored rounded to 6 decimals: a live entity is ~18.8 KB, a
sentence ~10.5 KB at 1024 dims. Seeds are float16 and running sums float32,
because float16 drifts on sums of thousands of unit vectors. In memory every
value is kept at its stored precision, so a restart never changes a result.

`MemBackend` (RAM) serves tests and lab paths; an unreachable cluster
degrades to it and never raises at boot. The bucket ladder (create, or
reuse a bucket left behind by another seat when the RAM quota is spoken
for) lives in `membrane/substrate.py`.

## Sharing

Each seat (origin) writes only its own events and states; reads merge.
`merged(eid)` is the seed plus the mass-weighted mean of every seat's
displacement; two seats that moved an entity in opposing directions
**diverge** (shown as `! seats diverge`). The working set is the seat's own
nearest states plus other seats' states it does not hold. The same claim
from two or more seats becomes a **commons** claim (`consensus`, or
`disputed` when a seat contradicted it), promoted at night. The old global
field's curated notes were converted into commons claims once. `membrane
move --efield` copies the collections between clusters or dumps them.

## The background mind

Each applied live event is a stimulus. The thinker digests it (its entities
as tags, an observation as the fn stamp), and user messages vote on the
dreamer's quarantined timeline branches. The dreamer proposes from the
model's recent sentences and vets a candidate by its surprise against the
entity states: on topic, but not already said. The NightClock (15 min) runs
the entity scripts; **births** (entities with mass ≥ 8 whose use drifted
from the everyday sense, announced once); **junk** (words said ≥ 24 times
that never took a direction); activity; the commons; and the coherence null
once a day. The capture worker's idle hook runs the claim judge and the A/B
judge on the idle local model.

## Building it from history

    membrane backfill            # = python -m membrane.backfill

The backfill writes the codebase first (code events and relations), then
the audit log in file order through the same intake. Sessions split at
30-minute gaps; records without timestamps take their line number as seq.
Each chunk is embedded and applied, and the byte offset is saved, so a run
resumes. A second run writes nothing. `finish()` numbers the events,
refreshes the coherence null and settles claims.

`membrane reembed --efield --embed-model M` moves the field to another
embedding model: every live event is re-embedded and re-applied.

## Evidence (e17–e25)

| experiment | finding |
|---|---|
| e17 | Whole-message recall buried answers: the restart-time answer ranked 21st; a real answer was MMR-dropped as a "duplicate" of an unrelated note at cos 0.91; a compact note contradicted the code (the port case). |
| e18 | A message's embedding can be rebuilt from its entities' states well above a random-entity null; tracking each entity by the **sentences that mention it** beats whole-message drift (68% vs 59% distinguishability). |
| e19–e21 | Filters for "which candidates are entities" gain little over random same-size sets; mass (agreeing evidence) is the stable property. So: every word is an entity, and the statistics decide what it means. |
| e23 | Coherence of a word's residuals separates project terms from filler; the everyday seed plus the project's sentences give specialization and activity. |
| e24 | Tool output mixed into conversation text poisons every word statistic. Split into discourse and observations, 77% of discourse entities have observed evidence. |
| e25 | Action and test claims can be checked mechanically (test-count claims match their run in ≥ 83%); the port note's contrary evidence existed only in the code, hence code events; mxbai is order-blind for relations (0.975), VSA role binding is not. |

## The entity-scripts covenant (2026-10-03)

Everything is an entity; the model sees entities; a script is a **part** of
its entity. The script's only ways outward are its own shape:

- **render** — the banner fragment (the heartbeat's visible line)
- **position** — the patch may move the entity in the field (unit-normed)
- **script_data** — its own data lane
- **`{run: true}`** — the engine execs the entity's own `command` prop
- (field-watch lane only) **links / unlink**

`membrane.speak()` is REMOVED from the script API (a legacy call strikes);
script `delete` is refused — retirement is `entity_edit`, the model's move.
Arbitrary prop-writes refuse (`patch_refused` in the apply's return).

**An error is an entity**: a raising or over-budget script bubbles as
`membrane::error:<fingerprint>` — a doc dropped at the errored entity's
position, refreshed while the error recurs (`n_hits`, `last_ts`), never
refreshed when fixed (so it drifts back in time on the updated axis by
itself). The event pool never hears engine lanes: what the model's
watchers watch is the model's own choice.

## Script budgets (the resource matrix)

| trigger | budget | pacing | gravity? |
|---|---|---|---|
| tick (heartbeat cron) | 0.25s hard (abandon + strike) | authored `tick_every` | yes: heat ×1–8 |
| mention (digest) | 0.25s | the digest's 10s | yes |
| watch (autonode listener) | 10s + the 4-thread pool, abandon+strike | event arrival (5s cooldown) | no |
| getter (read-back) | 1.0s | the read's 5s LRU | no |
| thought / dreamed | 1s / 2s | the hum / 60s idle | no |
| consolidation (nudge) | 2s | the 15-min pass | yes (every_n × gravity) |
| change (field-watch) | 0.25s + gravity + freeze | crossing + hysteresis | by design |

A tick is the hum's beat — one at a time, on the heartbeat thread; slow
ticks stall the pulse, hence the hard budget. Watchers run on the bounded
pool away from the pulse. Over-budget = the fire is ABANDONED (the script's
thread lands out-of-band; the per-entity lock holds the next fire) and the
overrun STRIKES — a slow-success counts, 3 strikes disable.

## Heat, gravity, freeze (the limiter)

Every entity carries `entity_heat` (0→∞): the max of its field-churn EMA
(updates/s) and its scripts' CPU EMA (`process_time` ms per run, so a
sleeping script never reads hot). Effects, continuous:

- **gravity**: the effective tick interval = authored `tick_every` ×
  (1 + k·heat), ceiling ×8 — hot entities tick rarer, cold exactly as
  authored. The consolidation `script_every_n` stretches the same way;
  the overrun gate reads the EFFECTIVE interval (a stretched cron can't
  wedge).
- **freeze** (heat over `MEMBRANE_HEAT_FREEZE`, default 1.5): the
  entity's field-watches PAUSE (`frozen_ts` stamped, visible) and ticks
  sit at the gravity ceiling; **melt** after `MEMBRANE_HEAT_MELT_S`
  (60s) under the bar — a suppressed burst then delivers as ONE grouped
  event (n_changes + span).

Knobs: `MEMBRANE_HEAT_COLD` (0.3), `MEMBRANE_HEAT_FREEZE` (1.5),
`MEMBRANE_HEAT_MELT_S` (60), gravity ceiling ×8, churn half-life 20s.


## The field-watch lane (2026-10-03): the entity watches itself

The model plants up to **3 field-filters per entity** (`entity_edit` with
`watch_fields=[...]`): the filters read the entity's OWN field state —
never the incoming streams. Fixed verbs, model-defined args:

| verb | arg | fires when |
|---|---|---|
| `pos` | sim distance | the position moved further than arg (chord distance from the last stamped position) |
| `align` | floor | coherence/net-align crossed DOWN through the floor |
| `vel` | speed | ring-velocity crossed UP past arg |

**Crossing semantics**: one fire per transition; return re-arms (the
stamps live in the entity's props — `wf_stamp_pos/align/vel`).

The field's flush IS the event source: every dirty pass checks the
filters, and a pass where a filter CROSSES delivers ONE `field:changed` event (kind/entity/verb/
n_changes/span) to the ordinary event pool — the drainer admits it like
any watch fire (3 slots, the loops' turns, the verdict stamps
`watch_last_decision`). The entity carries `change_script` — the reaction
runs under the same engine covenants (0.25s budget, gravity, freeze,
3-strikes); its verdict obeys the whitelist (position move, links for
this lane, render).

**Gravity's grouping**: a hot entity's crossings do not each fire — a
crossing opens a burst and the changes after it accumulate into it (edits
that cross no filter are silence, #567); when the heat subsides (or `WF_MAX_LATENCY_S`
= 120s passes — a forever-hot entity delivers a grouped event every 2
minutes, never silence), ONE event delivers (n_changes, span_s). Heat
over the freeze bar (`MEMBRANE_HEAT_FREEZE`, default 1.5) freezes the
filters entirely (`frozen_ts` stamped — visible in the entity's props);
melt happens after `MEMBRANE_HEAT_MELT_S` (60s) under the bar, and the
held burst delivers. Watching is the model's choice; the meter is the
field's law: hot means slower, frozen means quiet, both visible.

**Planting**: `entity_edit(eid, watch_fields=[{verb, arg}, ...])` —
refuses a 4th filter, unknown verbs, non-numeric args; empty list
clears. Attach/detach announce to the pool like every sensor's arming.


## Ollama's serial queue (the operator's note, 2026-10-04)

Ollama serves its models **one call at a time** (`OLLAMA_NUM_PARALLEL`
defaults to 1). Membrane's lanes all call the same Ollama: your turn's
model, the embedder (every capture), the claim/A-B judges, the
watcher's reads. A single slow call (a cold 14B load, a cloud relay's
hang) queues EVERYONE behind it — the turns read 30-115s "stuck", my
concurrent probes time out, and the room feels dead while the queue
drains.

**The honest fix is Ollama's env** (the operator's hand, in the app's
launch env):

    OLLAMA_NUM_PARALLEL=2      # the turn + one ambient lane live
                               # together; 3 = roomier, more VRAM
    OLLAMA_KEEP_ALIVE=30m      # the model stays loaded (the cold
                               # load's cost stops recurring)

Measured on the M2 Pro/32GB with qwen3:14b resident (9GB): a probe
behind a turn: 30-115s; alone: 1.2s (granite4). The queue is the
latency.
