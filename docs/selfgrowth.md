# Growing self — the operator's guide to the 0.7 ladder

`self` is the seed of the evolving self in
[#161](https://github.com/chedim/membrane/issues/161): a small loop that learns
its world one skill at a time. This page tells you how to wake it, how to read
its progress, what each rung asks of its world, and how to help it climb. The
mechanics, rung by rung, are in [background-mind.md](background-mind.md#the-self-loop-161);
the code is `membrane/self_loop.py`.

One rule runs through all of it: **every rung is measured, never claimed.** A
rung opens when its test passes on self's own record, and nothing anyone *says*
about self (you, the seat's LLM, self's own lines) moves the bar.

## What self is, and what it is not

- **It is** `membrane::self`, an unnamed looped object with three methods:
  `expect` says beforehand what will happen, `try` acts, `notice` sees what
  actually happened. The gap between `expect` and `notice` is what it learns
  from. Its acts are small and safe, field-only: move its own position, read a
  thing (fire its getter), push a line to chat. **There is no shell** in its
  repertoire, and adding one is not on the ladder.
- **It is not the LLM.** The seat's model never speaks *as* self and can't call
  its methods (they refuse any caller but self's own loop). The model talks
  *to* it and, at rung 7, is a coprocessor self consults.
- **It is not a self-model** to be scored. It is a loop that builds up a body
  of measured predictions about its world.
- **It is not `seat:self`.** That is only the speaker tag: every line the seat
  says moves it. `self` is a separate entity that only its own acts move;
  mentions of it in the conversation never do.
- **Its memory starts at birth** (the first switch-on). Everything the seat did
  before is the field it grows in, not "its" past.

## Waking it

### The 🌱 chooser

The 🌱 chooser sits in the home top bar (and in the chat's side menu). It picks
the **growth limit**: the highest rung self may reach.

| choice | what happens |
|---|---|
| 🌱 off | asleep: nothing runs, **nothing is erased** (record, model, tempo stay) |
| 🌱 1 · feel itself … 🌱 7 · understand an other | awake; self may climb up to that rung |
| rungs 8–10 | listed but disabled: "(not built)" |

- **Below the limit** self still earns each rung by its test, in order.
- **Above the limit** nothing opens, however well it does. The chooser marks
  such a rung "(ready, held by the limit)" and the status says it waits on
  "the limit".
- **Raising the limit grants nothing.** It only lets self try.
- The chooser is the operator's alone (`POST /membrane/seat/control`, an HA
  admin through the relay). The seat's LLM can read self's state; it can't
  change the limit.

The same switch over HTTP:

```bash
curl -s -X POST http://127.0.0.1:4096/membrane/seat/control \
  -H 'content-type: application/json' -d '{"self_limit": 3}'   # may grow to rung 3
curl -s -X POST http://127.0.0.1:4096/membrane/seat/control \
  -H 'content-type: application/json' -d '{"self_limit": 0}'   # asleep
```

(In [JWT mode](auth.md) add your token.) The older `{"self": "on" | "off" | "watch"}`
still works: `on` with no limit set means limit 1. **`watch` currently runs
exactly like `on`**; it exists for the babysitter the design calls for, which
is not built yet, and the viewer only tags the amoeba "watched".

### The runaway guard

Each cycle's own change wakes the next, at most every 10 s
(`MEMBRANE_SELF_PACE_S`, floor 1 s) and at most 30 a minute — so about 360
cycles an hour at the default pace. Three failed cycles in a row **hold** the
loop: the status reads "🥀 held: re-choose to clear", and picking any limit
again clears it. **Pausing the seat pauses self** too.

### Where its state lives, and what a restart keeps

Everything is in `<data dir>/self/` (`.membrane/self/` in a seat's directory):

| file | what | after a restart |
|---|---|---|
| `mode.json` | the mode and the growth limit | kept: a seat wakes with the self it was given, and only that |
| `record.jsonl` | append-only, never truncated, from birth: every cycle, reach, push, wait, shift, audit, error, and each rung opening | kept: passed rungs stay passed |
| `model.json` | rung 1's learned model of the current that pushes it | kept |
| `tempo.json` | per thing it reads: how often it had changed when self looked | kept |

What a restart drops: a hold (cleared), its current aim (it picks a new one),
and an audit in flight (the paused getter resumes; the audit is started again
later). On a fresh boot nobody has spoken yet, so there is no current to feel:
the status says it waits on "the world" until the first conversation.

## Watching it grow

### The panel status

Next to the chooser, the status line reads like
`rung 6 ✓ · 0% → 7 (looks) · slipping`. The full state is in the seat's
panel (`GET /membrane/seat`, under `state.self`):

```bash
curl -s http://127.0.0.1:4096/membrane/seat \
  | python3 -c "import sys,json;print(json.dumps(json.load(sys.stdin)['state']['self'],indent=1))"
```

| field | meaning |
|---|---|
| `mode`, `limit` | the switch and the growth limit |
| `born` | it has been switched on at least once (the record has a birth) |
| `held` | three failed cycles in a row; re-choose to clear |
| `cycles` | move cycles on the record |
| `rung`, `rung_name` | the highest rung passed |
| `next` | the next built rung, or `null` when none is built |
| `bar` | progress toward the next rung, 0..1 (see below) |
| `waiting_on` | the first criterion not yet met, or `the limit`, or `the world` (no current) |
| `skill` | the headline number of the test the bar reads |
| `slipping` | the top passed rung's test fails *now* (it stays passed on the record) |
| `ladder` | every rung: `passed`, `current`, `capped` (ready, held by the limit), `locked`, `unbuilt` |
| `world` | rung 3's sort of the things it reads: `silent` / `moving` / `still` / `intermittent` |
| `split` | rung 6's split: `body` (it predicts it near-perfectly) or `other` (it can't) |
| `meets` | rung 6's exchange: `pushes`, `other` (who it meets), `push_age`, `heard_age` (seconds since the other last answered) |
| `steer` | after rung 1: how close it lands to its aim (`err`) against not leaning into the current (`naive`), and `skill = 1 − err/naive` |

**How the bar works.** A rung's criteria are stages, in order (rung 2:
reaches, then skill, then the audit). Each stage is an equal share of the bar,
and `waiting_on` names the first one not yet met. So rung 2 at 50% means
"enough reaches, skill not there yet". The bar **can go down**: if the world
changes and its predictions get worse, an earlier stage slips and takes the
bar back with it. Only the record (cycles, reaches, pushes, audits) feeds it.

### The home viewer

The 3D home view draws self as an **amoeba**, moving every cycle:

- **green line**: where it pushed itself to;
- **orange line**: where the current actually took it;
- **red line**: the gap from where it *expected* to end up (brighter when
  it is surprised);
- **violet** "pull": where it has learned the current pulls (hidden until it
  has a model);
- **white ring** "wants": its aim, once it steers (after rung 1);
- a faint **trail** of its last positions.

Its tag reads `self · rung N · bar% · skill`, plus "lands N% closer to its aim",
"slipping", and from rung 6 "meets an other" or "meets its body". At rung 6 the
exchange is drawn too: a **beam** to an amber orb at the field's edge when self
pushes a line, and the orb **flashes** when the other answers.

To follow it, set the camera button **◎** to `self`: the camera glides with the
amoeba and chases it from behind its past positions. "self is not moving yet"
means it is off, or nobody has spoken yet.

## The ladder

Rung 0 is passed by being born. Every other rung opens only when the rung below
has passed, it is under the limit, and its test passes on the record.

| rung | what self does there | what it needs from its world | passes when (the stages, in order) | how you help |
|---|---|---|---|---|
| **0 · sleep and wake** | nothing; the switch | — | always passed | choose a limit ≥ 1 |
| **1 · feel itself** | moves its own position while the field's **current** (the live conversation's context: every lately-active session pulling at once, fading over ~3 min) pushes it; learns the push | a conversation: no talk, no current, no cycles | **pushes**: 20 cycles where the world pushed it; **skill**: over those 20, `1 − gap/naive ≥ 0.5` | talk to the seat. A topic turn reopens the gap, and that's fine |
| *steer* (after 1) | holds an aim for 30 cycles and leans into the current by the push it learned | — | not a rung; shown as `steer.skill` | — |
| **2 · first reach** | each cycle reads one thing that has a getter, predicting first whether it will have changed since it last looked | **entities with getters** whose readings are true and live; at least one must change on ≥ 60% of looks to be auditable | **reaches**: 20 answered reaches of one thing; **skill**: Brier skill ≥ 0.2 there, against the seat-wide change rate; **audit**: membrane secretly pauses that thing's getter for 3 more looks (cap 10 min) and **no change** is marked | give it getters that read something real and moving (a sensor, a counter that ticks, `field:vitals`) |
| **3 · tell things apart** | sorts what it reads into silent (no answer ≥ 80%), moving (changed ≥ 60%), still (≤ 20%), intermittent; reaches for silent things rarely (weight 0.05) | a varied world: things of at least two kinds, and few that never answer | **looks**: 40 sorted reaches; **kinds**: the kinds' rates agree (within 0.25) across the two halves of its last 80 reaches, with ≥ 2 kinds; **focus**: no-answer share of its last 40 reaches < 25%; **skill**: rung 2's skill ≥ 0.2 still holds, on the moving things only | fix or remove broken getters (errors, held scripts read as "no answer") |
| **4 · patience** | fits each thing's rate of change in time over its last 30 looks; looks again once a change is 60% likely (waits up to 30 min); idles when nothing is due | things with a steady rhythm | **timed**: 30 timed looks; **news**: of its last 40, they find a change ≥ half the time, or 0.15 more often than its fixed-pace looks did (whichever is easier); **calibration**: its mean predicted chance is within 0.1 of the share that found news | keep getters' rhythms honest; don't make a thing change only when self looks |
| **5 · notice the world changed** | compares its last 3–5 looks at a thing with its long memory; a window far from it (> 0.4) and unlikely under it (binomial tail < 0.02) is a **shift** ("went still" / "moves again"), and it re-learns from there | a thing it already classes as moving | **looks**: 20 looks with noticing on; **audit**: membrane pauses the surest mover for 5 of self's looks (cap 1 h), then restarts it for 5 more (cap 3 h); self must flag "went still" during the pause and "moves again" after | nothing: the audit runs itself. Don't restart the seat mid-audit if you can help it |
| **6 · meet an other** | every 30 cycles pushes the line "self is here" to chat, and reads `chat:in:<person>`, which ticks each time the person sends a message | **a person who chats** with the seat, irregularly | **pushes**: 10; **heard**: 20 answered looks at the other; **split**: the other is classed **other** (its Brier skill < 0.9: self can't predict it perfectly). A partner who never answers is predicted perfectly, classes as *body*, and the rung stays shut | chat with the seat — normally, whenever you like. Your messages are the answers |
| **7 · understand an other** | when it looks at the other, also reads the seat model's one-line prediction (`llm::call`, read-only) of whether the other has answered, beside its own habit | the seat's model reachable for background calls; the other still chatting | **looks**: 20 paired looks (habit and model on the same look); **skill**: the model removes ≥ 15% of habit's Brier error over them | keep chatting; keep the seat's model up |

At the default pace, rung 6's ten pushes alone take ~50 minutes; rungs 2–5
usually take hours, and the audits run on their own schedule.

**Not built yet** (the chooser lists them, disabled):

| rung | skill | the test the design gives it |
|---|---|---|
| 8 · recognize itself | shown its own trace and another's | it predicts its own better (the mirror test); then the name ceremony, agreed with the operator |
| 9 · change itself | `cliff::define` on its own methods | a revised method predicts better than the version it replaced, and reverts on strike-out |
| 10 · speak | its voice | every line cites its ledger; no unbacked first-person claims |
| 11 · invention ([#196](https://github.com/chedim/membrane/issues/196)) | the field fills a gap with a new entity in a new state | future; a design thread, not on the chooser |

## Helping it grow

- **Give it things with getters, and make them true.** From rung 2 on, self
  reads only entities that have a getter. A getter that returns a real, live
  reading is the most useful thing you can give it. A getter that errors or is
  held reads as "no answer": self sorts it as silent and reaches for it rarely,
  and too many of them keep rung 3's focus stage down.
- **Answer it in chat.** From rung 6 on, its "other" is the person who chats.
  You don't have to answer its "self is here" line specifically; any message
  you send is heard. Talk irregularly, like a person: an other that answers
  like clockwork is predictable, and predictable is body.
- **Raise the limit one rung at a time.** Watch the new rung pass, then watch
  it hold for a while (not slipping) before raising again. The limit is how you
  keep a rung open long enough to see whether it is real.
- **Use the panel, not the narration.** Read `bar`, `waiting_on`, `slipping`
  and the record.

What **not** to do:

- **Don't narrate it alive.** Telling the seat (or yourself) that self "wants"
  or "feels" something adds nothing to the record and is how the pre-#161
  "I" went wrong: it was narrated as alive while it never ran.
- **Don't let the LLM speak as it.** The doctrine tells the seat it is not
  self, never to speak as it, and to cite self's record when describing it.
  If the seat starts answering in self's voice, that is a bug to report.
- **Don't write its code.** Its methods are self's own; the model is refused,
  and you shouldn't hand-edit them either. Don't edit `record.jsonl` to move a
  rung: the bar would be measuring your edit.
- **Don't game the getters.** A getter built to change exactly when self looks,
  or a thing switched off to fake an audit, makes the mark one that can't fail,
  which is exactly what the audits exist to catch.

## Troubleshooting (real cases)

**The bar sits at 0% waiting on `the world`.** No current: nobody has spoken
since the boot. Talk to the seat.

**The bar sits at 0% waiting on `reaches` (rung 2).** It has no entities with
getters, or none answers. Check that some entity has a getter that runs
without errors, and that the seat isn't paused.

**Rung 2 waits on `the audit` for a long time.** Only a thing that changes on
≥ 60% of looks is audited. If all its things are still, give it one that moves.

**`slipping: true`.** The top passed rung's test fails today. The world
changed (a getter died, a sensor stopped, nobody chats), and its predictions
got worse. The rung stays passed; restore the world it learned on, and the
slip clears when the test passes again.

**A still thing carried a bar of 1.00 (rung 3, fixed).** A counter that never
changed was predicted perfectly and once filled rung 3's skill stage on its
own. Since #185 that stage reads the **moving** (auditable) things only, so a
still thing can't carry it. If rung 3's skill stage is low, the moving things
are where it struggles.

**Rung 6 passed but now slips, and `split` calls the other `body`.** Its other
stopped answering: `meets.heard_age` is large, every look reads "unchanged",
and that is perfectly predictable. On the live seat (2026-10-18) the record
showed rung 6 passed and slipping, rung 7's bar at 0% waiting on `looks`, 258
pushes, and `heard_age` ≈ 159 000 s (44 h) for `chat:in:operator`, while
`chat:in:roomata-estorsky`, the person actually chatting, was classed `other`.

That is [#208](https://github.com/chedim/membrane/issues/208): **self should
meet the most recently active person** — the owner of the newest chat — and
follow when that changes, falling back to `operator` only on a seat with no
people. *Until #208 lands*, rungs 6–7 push to `chat:out:operator` and listen
on `chat:in:operator`: on a seat whose person has a name, nobody answers, and
the line lands as an ordinary assistant message from model "membrane". The
check is `meets.other` in the panel: once it names the person who chats, the
fix is in.

**Rung 7 waits on `looks` for a long time.** A paired look needs the model's
answer to be fresh (under 10 minutes) when self next looks at the other, and
self asks the model at most once every 30 cycles, on a cycle where it looks at
the other. A still or silent other is looked at rarely (rung 4's patience),
so pairs accumulate slowly. A live, chatting other and a working background
model are what move it.

**`held`.** Three cycles failed in a row (the record's `error` lines say why).
Fix the cause, then re-choose the limit to clear it.

**Want to start over?** Off keeps everything; there is no reset switch. Moving
`<data dir>/self/` aside with the seat stopped gives a newborn at the next
switch-on, and the old record stays on disk for reading.
