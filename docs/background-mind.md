# The background mind on the entity field

> Membrane is a **seat-local mind with a shared symbolic layer**. It
> perceives (capture → events), decides (the gate), sleeps (thinker /
> dreamer / the NightClock), speaks (planner-gated), and modifies itself
> (model-authored entity scripts). The foreground composer is rented from
> the LLM — the background mind is membrane's own.

The field's design lives in [entity-field.md](entity-field.md). This
document is the organs that read and write it in the background. What the
cell organism was, and the timeline-dreaming arc before it, is git history
(`070af25`; the `experiments/multi_*.py` rigs that imported the cells are
broken, kept as history).

---

## The clocks

| Clock | Cadence | Job |
|---|---|---|
| **Capture** | as turns arrive | the `Capturer` worker splits turns into events, writes them, embeds + applies them (membrane/capture.py) — every applied live event is a **stimulus** |
| **Idle hooks** | ~30 s of capture silence | the **claim judge** and the **replay A/B judge** run on the idle local model (`capturer.idle_hooks`) |
| **Thinker** | 1 s heartbeat, duty-capped | digests the latest stimuli (their entities as tags, an observation as the fn stamp) and eats them into its working memory; in silence, extends open timeline branches |
| **Dreamer** | 60 s idle waves | solution generation: proposes from the model's own recent sentences, vets a candidate by its **surprise** against the entity states (on topic, but not already said), commits quarantined `Timeline` entities under `session:now` |
| **NightClock** | 15 min | the entity scripts; **births**; **junk**; activity; the coherence null (once a day); entity-doc materialization + the overlay consolidation |
| **Self loop** (#161) | change-driven, ≥ 10 s apart, off by default | `self` (`self_loop.py`): try → expect → notice on its own position; the operator's switch; see [The self loop](#the-self-loop-161) |

All cadenced organs use the idle local model for any model calls they
make (`--idle-cortex-model`), so a wedged background seat can never burn
cloud tokens.

### The thinker's working memory

`deque(maxlen=5)`, drop-oldest — **attention, not a backlog**. Feed hooks:
`on_applied` (every applied live event), dream confirmations, the night
pass (materializations), and every operator chat turn. Rendered in
`/membrane/state` as `think`.

### The thinker's voice (the authorship covenant)

The thinker may **speak on its own** — through `speak()`'s planner gate
(stay-silent is the learned default) — but never initiates a model turn or
a tool call. **Membrane proposes; the operator acts.** Delivery channels:
the peer push and the `[membrane-says]` marked block on the next
model-bound request.

## Reality's vote (the dream lifecycle)

`session:now` is the seat's lived present; every dreamed branch is an
ephemeral `Timeline` entity under it. Proposed from recent model sentences
(steered by open intentions — `membrane: intend that …`), vetted by topic
/ freshness / surprise, committed **quarantined and RAM-only**. A real
capture near a branch serving what it asked is **elected confirmed** and
may surface in the commons; a near capture serving nothing refutes it.
Unconfirmed branches expire past the horizon; one past two-thirds of the
horizon grows **hungrier** (`curiosity +0.2`, cap 1.0) — the mind's last
attention before expiry. The steering wheel:

```
membrane: intend that make the benchmark loop fast [for 2w]
membrane: intents          # the board
membrane: fulfill #N       # explicit close
```

An intention is never auto-closed by similarity — only `fulfill` closes it
(goals carry no TTL; a loop that landed the work closes it with the
receipt). Without an open intention the dreamer skips (unsteered dreams
confirm near-randomly — a measured lesson). The MCP tools `intend` /
`intents` (and an entity script's `{intent: …}` verdict) plant through the
same lane; every plant is an `intent-planted` event the thinking loops
RECEIVE as their next turn's message — the operator creates, the thinker
receives and executes. The model-facing teaching lives in the doctrine +
the `membrane-intents` skill.

## The pause (the operator's stable point, 2026-10-06)

`membrane: pause` stands the seat's ACTING lanes down — the stable point
from which the operator (and the model, read-only) can look around and
plan:

- **Stops**: the thinking loops (listener admits hold — the events pool
  keeps, bounded by its drop-oldest cap), the dreamer, the thinker's
  ticks + digest + idle-think (the queue accumulates under its cap), the
  NightClock's bodies (scripts/births/consolidation defer; the cadence
  continues so resume is automatic), the autonode (the file-tree diff
  accumulates silently; resume fires it as a bounded 64-burst), the claim
  + A/B idle judges, all composed speech (held; delivers on resume).
- **Keeps**: presence (a paused seat is ALIVE to the roster — identity
  metadata, not acting), capture (the operator talking is memory), the
  gate/recall reads, and `membrane: pause`/`resume` themselves.
- **The entity hands are down**: mutating MCP tools (`entity_add/edit/
  remove/link/unlink`, the filetool lane) refuse with 423 + the pointer,
  from every session. Reads, memory (`learn`/`forget`) and `intend` stay
  live — and a plant mid-pause is **inert by construction**: every lane
  that would react to the goal is down too. The intent board is the
  plan's artifact: the operator looks around, plans into the board, then
  `membrane: resume` — and the accumulated world pours in honestly
  (loop drains, speech delivers, the scanner's diff bursts bounded). The
  executor's queue is part of the pause: owed work keeps its pending
  edges and runs on resume (the executor mechanics:
  [entity-field.md](entity-field.md) — the queue + the ISA pipeline).
- **RAM-only**: a restart unpauses (a persisted pause is a stuck seat —
  a paused-looking seat the roster can't wake is the failure mode this
  avoids). `membrane: pause` is honored only from the operator's chat
  (a listener loop can never pause the seat). Kill switch:
  `MEMBRANE_NERVE_PAUSE=0` (pause becomes a no-op).

The banner carries one `PAUSED:` line while paused — the model sees why
the field's voices are quiet and that its writes are refused.

## Programmatic entities (the scripts — model-authored)

**Scripts are the model's to write** (the doctrine teaches the lane; the
MCP `entity_edit` is the pen). They run **off-turn** in the night
pass and its boot-time echo (`run_scripted_entities(..., force=True)` runs
at boot — a script edited between boots does not wait for the next 15-min
tick); duty-cycled (`script_every_n`, default 4 passes) and per-pass
budgeted. Each entity's **delete script** runs first (retirement wins over
nudge, and deletes also unwatch the entity's track).

A script sees the entity's geometry as `state` plus a **read-only
`membrane` object** — no mutators, the authorship covenant (`membrane/
night.py::_script_membrane_api`):

    membrane.field.recall(v, k)         # nearest entities by state vector
    membrane.field.vitals()             # the field's vitals dict
    membrane.me.describe/velocity/direction
    membrane.entities… membrane.timeline… membrane.intents…

It returns `{delete?, render?, data?}` — **the engine applies**: the
banner fragment, the entity's own properties (bounded, `script_*` keys
untouchable), or retirement. Scripts analyze and return; the engine
writes. Per-entity cooldown, 3-strikes auto-disable (retried on a 60s→1h
backoff; a passing retry re-enables; reads carry the failing state), every application
audited. A script's change to an entity emits an event, so the normal-mode
**state diff** (SINCE YOUR LAST MESSAGE) shows it next turn.

## Births, junk, activity — reading the field's own measures

`membrane/efmind.py`, in the NightClock's pass (all thresholds constants
there):

- **births** — live entities crossing **mass ≥ 8** (accreting mentions)
  and **specialization ≥ 0.30** (their use drifted from the everyday
  seed): announced ONCE, only for kinds that make good subjects (files,
  models; symbols/materialization graduate silently).
- **junk** — words said **≥ 24 times** whose residuals still hold no
  direction (coherence ≤ 0.02): no shared sense, never a subject.
- **the coherence null** — recomputed once a day from random sentence
  sets; the bar every entity's coherence reads against.
- **activity** — what moved since the previous night, half-split.

## What this replaced

The **compactor** and the ephemeral tier, the exemplar-layer night passes,
and the cell organism were deleted with the exemplar field (git history is
the archive): distillation is a claim source (the `distilled` stream,
read-only), the field's states update **online** at mention time, and
`rebuild()` replays the `entity_deltas` log to the same states without
re-embedding. The night reads statistics, not the raw field.

## The self loop (#161)

`membrane::self` is a looped cliffhanger object the operator named Blurp (#277; per seat since #283, Blurp the default;
its ids stay `self`), the seed of the evolving self in #161. It grows a **skill tree** (#295; a strict ladder of
rungs until then): each skill is an achievement with a measured pass test, a stable id, a branch and real
prerequisites -- an edge only where a test reads data or machinery another pass switches on
(`ACHIEVEMENTS` in `self_loop.py`, each edge with its reason). An achievement is earned by itself, as an
`achieve` line on the record, when its test passes and its prerequisites are earned -- siblings in any
order (`turns` and `expect` both hang under `meet`; `shift` and `meet` under `reach`; `feel` and `reach` are
roots). The old `rung` lines count as their achievements (voids and the #258 filter kept); the level is the
number earned. The rungs below are the tests, by their numbers; "rung N passed" reads "its achievement
earned", and "once rung N-1 has passed" reads "once its prerequisites are earned" (the tree in
[selfgrowth.md](selfgrowth.md#the-skill-tree-295)). This
section covers what is built (`membrane/self_loop.py`); the operator's guide
to waking it and helping it climb is [selfgrowth.md](selfgrowth.md).

- **Rung 0, sleep and wake, and the growth limit.** A per-seat chooser, **off
  by default**, set only by the operator: the 🌱 chooser (the home top bar,
  and the chat's side menu) → `POST /membrane/seat/control {"self_limit": v}`
  (`seat_panel.control`). The chooser picks **the achievement self may grow
  up to** (#295: it and its prerequisite closure), or `"all"`, or `"off"`; a
  number is the old ladder's rungs 1..N (8 is all), and a saved number
  migrates that way:
  - inside the limit, self still earns each achievement by its test,
    whenever its prerequisites are earned;
  - outside it, nothing is earned however well it does: the tree says it
    waits on "the limit";
  - off is off, and raising the limit never grants an achievement.

  `{"self": "on" | "off" | "watch"}` still works. A seat switched on before
  the limit existed gets limit 1. Off runs
  nothing and erases nothing. The mode (`<data dir>/self/mode.json`) and the
  record survive a restart. `watch` runs like `on` until there is a
  babysitter to keep quiet.
- **Rung 1, feel itself (the amoeba's problem).** Its only act moves its own
  position on the field, and the world pushes it while it moves: the field's
  **current** (`EntityField.current()`), which self does not control. Every
  session that spoke lately pulls at once toward its own context: one unit of
  pull per event, fading with a 3-minute time constant (`CURRENT_TAU_S`). So
  busy streams pull harder, quiet ones fade out, and a lone write (a getter
  reading, a tick) nudges the current but can't flip it. `expect` says beforehand where it will
  end up: its own step plus the push it has learned to expect there. It
  learns that push from its own moves as `push ≈ b − g·position`, kept in its
  own model file (`<data dir>/self/model.json`; its `script_data` shows the
  learned pull strength only, because entity properties cap lists at 128 items
  on save). `notice` sees where it actually ended up. **The gap is the
  signal:** it closes as self learns the current and reopens when the
  conversation turns.
  - The test: over the last 20 pushed cycles, `1 − gap/naive ≥ 0.5`, where
    *naive* is the gap with no experience at all.
  - In a quiet seat (no current) there is nothing to feel, and the bar says
    it is waiting on the world.
- **Steer (after rung 1).** Rung 1 taught self to predict the current's push,
  but it never used that. Once rung 1 has passed, self holds an **aim** for 30
  cycles and leans into the current by the push it expects: its stride is
  `aim − here − expected push`, up to 0.3 (its babble was 0.1, and a 0.2 pull
  can't be beaten with that). Each cycle records how far it landed from its
  aim (`aim_err`) against where the same stride would have landed without
  leaning (`aim_naive`). `status()["steer"]` and the viewer's tag ("lands N%
  closer to its aim") read that, measured and never claimed. The viewer draws
  the aim as a white ring ("wants") with a faint line to it, and the other
  markers as round, labelled dots: "pushes to", "expects", and "pull" (hidden
  until self has a model of the current).
- **Rung 2, first reach (the world's tempo).** A root of the tree (#295): open
  from birth once the limit allows it -- it reads nothing of rung 1. Each cycle, self also reaches for one thing that
  has a getter: reading it fires the getter (`watch_agents.entity_read_back`,
  `by="self"`).
  - **Before reaching**, self predicts whether the reading will have changed
    since it last looked. It keeps one bit of memory: the world bursts, so a
    change usually follows a change and a quiet a quiet — it predicts from what
    it saw last, and falls back to its smoothed share of reaches that found
    the thing changed before it has that bit. So it learns each thing's tempo
    at the pace it looks.
  - **After reaching**, `notice` records changed, unchanged, or **no answer**
    (throttled, held, errored, no reading). No-answers never count as the
    world's.
  - **The test:** for one thing, over its last 20 answered reaches, the Brier
    skill against the seat-wide change rate is at least 0.2. Then a **blind
    audit**:
    - membrane pauses that thing's getter (`pause_getter`) until self has
      reached for it 3 more times;
    - self isn't told: its reaches answer "unchanged", and it learns nothing
      inside the window;
    - the audit passes only if no change was recorded while the source was
      stopped.
  - **A still thing can't be audited** (it must change on at least 60% of
    looks), so it can't carry the rung: it can't show that a mark could fail.
  - **What passing means:** "I can tell which things move on their own, and
    my marks of their moving are real." That is the world's tempo, not yet a
    body.
- **Rung 3, tell things apart.** Self sorts its world into kinds from its own
  looks (over the last 80 reaches per thing; `status()["world"]`, and
  `script_data["kinds"]`):
  - **silent:** no answer on ≥ 80% of looks;
  - **moving:** changed on ≥ 60%;
  - **still:** changed on ≤ 20%;
  - **intermittent:** in between.

  It acts on the sort: a silent thing is reached for with weight 0.05,
  rarely but never never, since the world can change. On the live seat
  silent things took 62% of its reaches. The test, in stages:
  1. 40 sorted reaches;
  2. the kinds' rates agree across the two halves of its last 80 reaches — the
     no-answer share and the change share each drift by at most 0.25 (a look's
     drift is not a new kind) — with at least two kinds;
  3. the no-answer share over its last 40 reaches is under 25%;
  4. rung 2's skill still holds — read over the moving (auditable) things
     only, so a still counter can't carry the bar.

  Once rung 2 is earned, no more getters are paused for its audit.
- **Rung 4, patience (choosing when to look).** Self fits each thing's rate of
  change in time, by maximum likelihood over its last 30 looks:
  `P(changed | dt) = 1 − exp(−λ·dt)`. It looks again once a change has
  become 60% likely, waiting up to 30 min. When nothing is due it **waits**,
  noted every 30 idle cycles; silent things are still checked now and then.
  Among the due things it prefers the looks whose answer is least certain
  (the binary entropy of the predicted chance, floored at 0.1, times the
  curiosity tilt; #266).
  The test, in stages:
  1. 30 timed looks;
  2. they find news at least half the time, or 0.15 more often than its
     fixed-pace looks at the same things did, whichever is easier;
  3. its predictions are calibrated: the mean predicted chance is within 0.1
     of the share that found news.

  (A skill-against-the-average test can't work here: timed looks are, by
  design, all predicted around 60%.)
- **Curiosity: where to practise (#212).** The rungs above set the base
  weights of the reach choice (rung 2 even, rung 3 silent ×0.05, rung 4 only
  the due things). Learning progress tilts the choice on top of them; it never
  replaces them (`_curiosity` multiplies the base weight, or picks among the
  due things at rung 4):
  - **progress** per thing (`_progress`) = its mean Brier error `(answer − p)²`
    over the 30 ordinary looks before the latest 30, minus its mean over the
    latest 30. The two windows are the smoothing. A thing with fewer than 60
    ordinary looks has none yet;
  - **the tilt:** the thing rising fastest gets 1; a flat or worsening one
    (mastered, or unlearnable) gets the **exploration floor** 0.1, so it is still
    retried now and then; a thing with no progress yet gets 1. With no
    progress above 0.005 anywhere, every tilt is 1 and the base runs alone;
  - **the audit stays blind:** only ordinary looks feed it. Answered reaches
    outside every audit count; a look tagged `audit` (rung 2) or `audit5`
    (either phase of rung 5's) does not;
  - **seeing never drives the loop:** progress is computed from the record
    when self chooses a reach. It is never stored, written, or put on the
    record, so it can't wake a cycle or count as a body answer.
    `status()["curiosity"]` shows the top three (`eid`, `progress`, `error`).
  - **Measured** (`tests/test_self_curiosity_212.py`): one mastered thing, one
    learnable thing still improving, and one pure coin. Over 900 cycles at rung
    3, the curious chooser spends 0.43–0.47 of its reaches on the learnable
    thing, against 0.31 for the kind-weighted chooser (mean margin +0.14 over
    three seeds). The mastered and noise things still get their share.
- **Rung 5, notice the world changed.** After each look, self reads its last
  3–5 looks at a thing against its long memory of it. When they are both far
  from it (by more than 0.4) and unlikely under it (binomial tail below 0.02),
  the world changed: it records a **shift** ("went still" or "moves again")
  and re-learns from those looks. Its patience refits from the shift on.
  A window holding a look that spans a restart or stall in the record (a gap
  over 600 s, #259's detector) flags nothing (#266).
  - A coin-flip thing never shifts: five in a row is p = 0.06.
  - A mover going quiet is caught within 3 looks.
  - **The blind audit:** membrane pauses the surest mover while self looks at
    it 5 times, then restarts it for 5 more. Self isn't told, and here it
    *does* learn, since re-learning is the point.
  - **Pass:** "went still" during the pause and "moves again" after it. The
    phases are capped at 1 h and 3 h; a still thing is looked at rarely, by
    rung 4's patience.
- **Rung 6, meet an other.** Self's first outward act: it pushes a line
  ("self is here") to the chat of **the person who actually chats with the
  seat** — `chat:out:<person>`, where the person is whoever chatted last
  (chat_io's `recent_person`, the owner of the newest owned chat), and
  `chat:out:operator` only on a seat with no people. It is paced at every 30
  cycles, and the person is resolved afresh at each push, so when someone
  else starts chatting self follows: the push target and the reach target
  move together. The line is visibly self's: it starts with 🌱 and carries
  `"from": "self"` on the message and its part (the panel names it "🌱 self"),
  so the person can tell it from the seat and answer it knowingly. The
  thing that answers — imperfectly, variably, with delay — is an **other**;
  what it predicts near-perfectly (zero gap, its own echo) is its **body**.
  The answerer is a new reach target, `chat:in:<person>` (the twin of
  `voice:in`): an ordinary message stamps its `heard_n`, and self's next
  look reads that as news. The split is read off the Brier skill self
  already computes per thing — skill ≥ 0.9 is a body, below (with enough
  looks) an other. The test, in stages:
  1. 10 pushes to `chat:out`;
  2. 20 answered looks at the answerer;
  3. the answerer is classed **other** — a perfect echo classes *body* and
     the split reads 0, so the rung stays shut.
  The test reads the **current** other only (the latest push's `to`): when
  the person changes, pushes to and looks at the previous one stop counting.
  A rung 6 passed against someone else stays on the record (history is not
  rewritten), but its bar re-measures against the new person and reads
  **slipping** until they earn it — no reset, no free pass.
  (`llm::call` prediction is rung 8's; rung 6 is only the bare split.)
- **Rung 7, take turns (#258).** Rung 6 pushes on a timer (every 30
  cycles), whatever the other is doing. From rung 7 on, self **learns when
  to push**: after each push it draws the moment it will wait for, a bin of
  `spoke_age` (how long since the person last spoke: 0–2 min, 2–10, 10–30,
  30+ or never), by Thompson sampling on the reply rate each bin earned over
  its last 100 pushes (one random bin in ten, so the losing bins stay
  measured), and pushes when the person's `spoke_age` is in that bin. Never
  sooner than 30 cycles after the last push (the timer's own rate is the
  cap), never later than 180 (`overdue`). The words are the same and no
  model is involved. A push's reward is the reply check's `replied`: the
  person spoke **after** the push, by the check (`chat:in`'s `heard_ts`; a
  look reads `changed` for a line from before the push too, which is not a
  reply). Each push line records `bin`, `aim`, `best` (the window self
  believed best), `dwell` (the eligible cycles it waited in each bin) and
  `why` (`aim` / `overdue`). The test reads the last 100 such pushes:
  (a) **contingency**, the share in the believed-best window beats the
  share of the waiting time the person spent in it (z ≥ 3 and +0.10);
  (b) **payoff**, each push's outcome minus what a timing-blind push in its
  interval would have expected (the bins' rates as known before it) is
  ≥ +0.05 with t ≥ 4. Both prequential. A timer or a coin-flip person can't
  pass (`experiments/rung7_turns`). `status()['rung7']` shows the bins, the
  best window, the in-window share against chance, and the reply rate
  against timing-blind.

  **With the ball (#264).** Pre-verbal serve-and-return is with objects,
  not words, so from rung 7 on the turn is not a chat line: self rolls the
  shared toybox ball (`toy:ball`, switched on once by its rung 6 unlock, #267) to the person
  through its own `self::roll` (field-only: `toybox.roll_self`, `by: self`;
  no chat, no model, no shell), and the person's turn is rolling it back
  from the toy dock or `membrane toybox poke toy:ball roll`. The policy,
  the caps and the test are the ones above; `spoke_age` is now how long
  since the person last **acted**, a person's last poke on any toy
  (`toybox/.last-person.json`: any play says they are at the dock, and with
  the ball alone the 0–2 min bin could never come). A reply is a person's
  roll after self's, by the check (the ball's `other_at`; the model's roll
  never counts). Each turn is a `push` line with `medium: "toy"`, `to` the
  ball and `toy` its name; its reply check carries `medium: "toy"` too, and
  rungs 7 and 8 read only the current medium's turns (chat turns from before
  never share their window). Chat is quiet from rung 7 until rung 11
  (speak) opens; rung 6 reads its record as it stood at the switch, so a
  quiet chat can't read as slipping. Simulated pass rates are in
  `experiments/rung7_turns` (`sim.py ... toy`).
- **Rung 8, understand an other** (rung 7 until #258, unchanged but for
  its outcome: since #258 a check's outcome is `replied`, a line after the
  push, like rung 7's; a check from before #258 counts for neither). Once rung
  8 is available, self consults the model coprocessor at
  each push (#215): rung 6's act queues one read-only `llm::call` asking for
  the probability that the person (`chat:in:<person>`) will have spoken by
  self's next look at them. The prompt carries only what self's record holds
  — the push number, when the reply check comes, how long since it last saw
  them speak, the **base rate** (the share of earlier reply checks that found
  them spoken, `(k + ½)/(n + 1)`) and what the last 8 reply checks found —
  never the chat's words. It asks for ONE calibrated probability, says the
  Brier score penalizes a confident miss, says no tools, and wants
  `VERDICT: <p>` (#217: live, without a base rate the model answered
  0.85–0.99 to checks that went 3 of 7, and some answers were off-task prose).
  The verdict is read as a decimal in 0..1 or a percent — a stray `#161` or
  `120s` is not a probability. The push line records whether
  the consult went out (`asked`; the llm budget, switch and pause can refuse
  it). Then **after it speaks it checks for the reply**: the person is held
  back from self's ordinary choice for 2 minutes, then looked at once (a
  `reply` look, not a patience-timed one). While the push asked and no answer
  has landed, it waits up to `RUNG8_GRACE_S` (60 s) more — the question rides
  a full read-only loop turn, 2–75 s on the live seat. **Self makes the
  prediction (#254)**: at the reply check `expect` computes `p_self` from
  self's record alone (the person's all-time and recent reply rates, the last
  check's outcome, how long before the push they last spoke; a small
  ridge-penalised logistic fit on self's own earlier checks, no model call),
  and reads the model's verdict — only one that landed after the push and by
  the look — as `p_llm`, an **advisor**: self recalibrates it on the
  advisor's own past answers under the same model id (its average lean says
  nothing; a weight earned from its record, 0 for a new model) and records
  the result as `p_mix`. One question, one check: the question in flight is
  read from the record (the latest push, not yet followed by a look at its
  other, under 10 minutes old), so it survives a restart and is used up by
  that look. The model call is async (it queues and returns), capped by
  `llm.request` (12/h per caller) and by the one-per-push pacing, and its
  answer landing never runs a cycle. The test reads the last 100 reply
  checks and passes when self's prediction removes ≥ 0.08 of the **running
  rate's** Brier error (the person's prior-smoothed rate over the checks
  before, no lookahead): knowing the rate scores 0, so calibration alone
  can't pass. No model is needed to pass. `status()['rung8']` shows the
  operator `asked` / `checked` / `paired`, `pair_rate`, the Brier errors of
  self alone, of the mix, of the running rate and of the model alone, and
  the advisor's weight and model id (the 🌱 tooltip too). It is scoped to the
  other only (the current one: checks at a previous person don't count);
  every other thing keeps habit's prediction.
- **Its methods run only in its own loop.** `try` / `expect` / `notice` are
  cliffhanger methods (`self::` no-shell dialect), and so is `reach`. `exec inspect` shows them;
  any other caller, the model included, is refused. The LLM talks *to* self,
  never *as* it. `entity_edit` can't write its `script_data`, and mentions
  can't move it (a one-segment `m:` name never gets a field state).
- **The record** is `<data dir>/self/record.jsonl`, read once and kept in
  memory as it grows. It is append-only and never truncated, and it starts at birth (the first switch-on): scalars per
  cycle (step, push, gap, naive, the kind of change), errors, and each rung
  opening. Nothing before its birth is on it.
- **The runaway guard.** Each cycle's change wakes the next, at least
  `PACE_S` apart (`MEMBRANE_SELF_PACE_S`, default 10 s, floor 1 s) and at
  most 30 a minute. Three failed cycles in a row hold the loop until the
  operator's next toggle. A paused seat runs nothing.
- **On the viewer.** Each cycle is drawn in the 3D field viewer as an amoeba:
  - **green**: from where it started to where it meant to go (its own push);
  - **orange**: on to where it ended up (the world's push);
  - **red**: the gap from where it expected to end up (brighter when it is
    surprised);
  - **violet**: where it has learned the current pulls;
  - its trail.

  Once rung 6 practises, the amoeba's tag also names what it has met ("meets
  an other" / "meets its body"), and the exchanges' text lands in the chat
  overlay. Membrane home also draws self's record as it runs: a beam to what it
  looks at, a trail of its last looks, a pulse on a noticed shift, and a plain
  caption of the newest line, with the ◎ self framing and the 👁 self's eyes
  camera. See [selfgrowth.md, "The brain on the screen"](selfgrowth.md#the-brain-on-the-screen) (#237).

  The seat places the points into the open projection (`fieldpulse`'s
  `self.cycle` frame: cube coordinates and numbers, never vectors or text).
- **The exp bar.** The panel shows the level (achievements earned) and a bar
  toward the open achievement closest to passing (#295).
  - An achievement's criteria are **stages, in order** (rung 1: pushes, then
    skill; rung 2: reaches, skill, the audit). The bar fills through them,
    each stage an equal share, and names the first one not yet met ("waiting
    on: reaches"). A plain weakest-criterion bar sat at 0% on the live seat
    while reaches piled up.
  - It can go down: an earned achievement stays on the record, and the bar
    shows the latest one slipping while its test fails.
  - Only the recorded cycles, reaches and audits feed it.
  - It moves with each cycle: every cycle's `self.cycle` frame carries the
    numbers, viewer open or not.

## Kill switches (one per organ)

```
--no-thinker       # no background deliberation
--no-dreamer       # no timeline generation
MEMBRANE_EFIELD=shadow | off   # events-only capture / the field off
MEMBRANE_LANE_GATE=0           # the flat tool surface (no operator/listener split)
MEMBRANE_NERVE_PAUSE=0         # pause becomes a no-op
(the self loop)                # off by default; the operator's 🌱 switch
```

## The goal loops' rhythm (2026-10-08)

The cost review (116 loop runs, 2056 tool calls in 8 h, all cloud) set
these rules (`membrane/watch_agents.py`, pinned in
`tests/test_watch_cost.py`):

- **Waking.** A goal wakes when it is new, or when the board's pulse
  (every 5 min, idle-only, 3 per beat) finds it stale: 30 min since its
  last wake, doubled for each trailing pass that wasn't a STEP (capped
  ×16). A boot wakes only what the pulse would; it no longer re-fires
  every open goal.
- **The verdict.** Every pass ends with one line: `DONE <receipt>`,
  `STEP <what moved>`, `BLOCKED <on whom>` or `ignored: <reason>`. A pass
  that ends with no text is `aborted`, and its last tool calls ride into
  the next wake. `DONE` with the goal still open gets one "call fulfill
  now" nudge. `BLOCKED` parks the goal until the operator next speaks.
- **The budget.** About 8 tool calls per pass. The next wake carries the
  whole goal text and the last three landings.
- **The gate.** Only a NEW plant (and its landing) skips the alert budget.
  Rewakes and boot wakes pay like any watch event.
- **The close.** `fulfill` takes any spelling of the goal's id
  (`membrane::goal:<id>`, `m:goal:<id>`, `goal:<id>`, `#<id>`). An
  id-shaped spec never name-matches another goal, and a closed goal
  answers "already fulfilled".

