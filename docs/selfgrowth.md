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

Its toys' state lives next door, in `<data dir>/toybox/` (see [Toys](#toys)),
and is kept across a restart too.

What a restart drops: a hold (cleared), its current aim (it picks a new one),
and an audit in flight (the paused getter resumes; the audit is started again
later). On a fresh boot nobody has spoken yet, so there is no current to feel:
the status says it waits on "the world" until the first conversation.

## Watching it grow

Everything below is in the seat itself: the exp bar, the 👶 panel laid out as
a character sheet, and the toy dock (#249). The external "Self's record"
dashboard that used to be rebuilt hourly by a script outside the seat is
retired; its numbers (working hours, the bug windows, the growth curve
against the fastest possible and a child) are computed by the seat now.

### The exp bar

Next to the chooser, self's progress reads like a game's experience bar
(#230):

```
Lv 6 · meet an other  [██████░░░░] 64%
→ 7 · understand an other · waiting on: pairs
```

- **the level** is the highest rung passed, with its name;
- **the bar** fills toward the next rung from `bar` (the staged test, below),
  with its percent; the caption under it names the next rung and what the bar
  is `waiting on`;
- **slipping** tints the bar amber (the top passed rung's test fails now);
- **held** greys it ("re-choose 🌱 to clear");
- **capped**: the next rung is above the growth limit, so the bar is hatched
  and reads `capped` ("raise 🌱 to let it try 7");
- **asleep** shows only `🌱 asleep`;
- **a new rung** gets a short level-up moment: the level turns gold, the bar
  glows once, and the caption reads `⬆ level up` for a few seconds. With
  *reduce motion* on, the gold and the caption stay and nothing pulses.

**The skill at a glance** (#249): under the bar, the number the bar's test
reads against the line it has to beat, e.g. `skill −0.13 / +0.15` at rung 7
(the model's Brier skill over habit, which must reach +0.15). Before anything
feeds the test it reads `skill not measured yet / +0.15`, never a made-up 0.
Rung 4's headline (the share of timed looks that find news) has a moving bar,
so it shows the skill alone.

The bar's tooltip still carries the long form (mode, limit, cycles, rung 7's
pairs). On a phone the caption row is hidden to keep the top bar short.

### The 👶 panel: self's character sheet

A 👶 tab sits at the left edge of the home view (clear of a phone's notch).
It opens self's side panel: a column beside the field on a wide screen, a
full-screen overlay on a narrow one. Open or closed is remembered per browser.
Esc or × closes it.

The panel is laid out like an RPG character sheet: its structure, not its
styling (membrane's own look, light and dark). Each section folds; the
nameplate, Attributes and the Quest log start open, and which sections you
open or close is remembered per browser.

- **The nameplate:** the name (`self`, until the name ceremony), **Lv** (the
  rung passed), the live exp bar, **the skill at a glance** (the number big,
  the line it has to beat, its change over the last hour and a sparkline of
  the last four hours: the same test run over the record as it stood every
  half hour), its age (hours since it woke) and its **working hours**.
- **Attributes:** one measured bar per built rung, with its rung number and
  value; a pass line marks what the rung's test needs.

  | attribute | rung | the number |
  |---|---|---|
  | Feel | 1 | 1 − gap ÷ naive over its last pushed cycles (pass ≥ 0.5) |
  | Reach | 2 | Brier skill of its looks at one thing against the seat-wide rate (≥ 0.2) |
  | Sort | 3 | the answered share of its last 40 steered looks (≥ 0.75) |
  | Patience | 4 | calibration error: \|mean predicted chance − share that found news\| over its last 40 timed looks (≤ 0.1; lower is better) |
  | Notice | 5 | the shifts it noticed, of the two its blind audit probes (1) |
  | Meet | 6 | rung 6's staged test: lines sent, answered looks, the other read as other (1) |
  | Understand | 7 | the model's Brier skill over habit at predicting the other's replies (≥ 0.15) |

  A skill can be negative: its bar then grows left of a zero line, in amber.
  A passed rung whose test fails now says **slipping**. A number nothing
  measures yet says "not measured yet", never 0.
- **Quest log:** the current rung's staged criteria with their counts and
  skill (`20/20 reply pairs ✓ · skill vs habit −0.34 → +0.15`), and what it
  is waiting on.
- **Achievements:** each rung passed, with its date, the working hour it
  passed at, and the skill on its pass record. Rung 6 says it was first
  passed against the empty operator slot (#208) and counts its hours from
  when its test first held with the person who actually chats.
- **Growth curve:** rungs over working hours (log scale; inline SVG). The
  solid line is working hours, the faint one the wall clock, the dashed one
  the fastest possible (each built rung's test minimum at the loop's pace,
  from `self_loop`'s constants), and the open points a child: the age a child
  typically shows the closest ability, rescaled by one factor (the median
  hours of self per child month over the rungs passed past rung 1). **The
  child scale is an analogy**, and the panel says so.
- **Where the time went:** hours alive = working + blocked by a known bug +
  not running (the seat down, restarting, or self asleep), as a stacked bar,
  with the bugs listed.
- **Inventory:** each toy with its health dot, kind, looks, its prediction
  error on the earlier half of its looks against the later half (falling
  means it is learning), and who changed it (the toybox's ground truth: self,
  world, the model, people). Play happens in the dock (below).
- **Relationships:** the others it has met: the people it sent lines to and
  looked for answers from (lines, looks, how often they replied, its Brier
  error on them: 0 is perfectly predictable, 0.25 a coin), the seat's model
  (rung 7's predictor: its Brier against habit's) and the toy players (how
  often they changed a toy, and self's error on the looks after); then the
  body set and the others, from rung 6's split.
- **Activity:** self's last 15 record lines, newest first, with how long ago:
  `reach w:fast → changed (p 0.98)`, `push → roomata-estorsky (asked the
  model)`, `shift noticed on w:prime (0.8 → 0)`, `rung 3 passed · tell things
  apart`. Fetched every 4 s, only while its section is open.

**Working hours.** Running time is the time self's record kept growing: a gap
over 2 minutes between two lines, and the time since the last line, is time
self was not running. From running time the windows a **known bug** held a
rung shut are taken out. They are a small data list in `membrane/self_sheet.py`
(`BUGS`), each with its fix time:

| bug | window | ends |
|---|---|---|
| #191 | rung 4 passed (rung 5 could not notice a shift at the audit's floor) | 2026-10-16 21:43:37 UTC, the fix live |
| #208 | rung 5 passed (rung 6 talked to the empty operator slot) | self's first line to a real person |

A window applies only on a seat whose rung passed before the fix; a seat
born later never had the bug and loses no hours to it.

The sheet comes from one read-only route, computed from the record,
`status()` and the toybox and cached for 30 s (behind the JWT gate like every
route; the HA relay forwards it on GET only):

```bash
curl -s http://127.0.0.1:4096/membrane/self/sheet | python3 -m json.tool | head -40
# {"header": {...}, "attributes": [...], "quest": {...}, "achievements": [...],
#  "growth": {...}, "time": {...}, "inventory": [...], "relationships": {...}}
curl -s 'http://127.0.0.1:4096/membrane/self/recent?n=30'   # the activity: {"lines": [...]}, newest first, at most 100
```

**It is read-only for self.** Nothing in the panel changes self; the 🌱
chooser stays the only switch. Everything it shows is `status()`, the toybox
and self's own record: nothing is claimed about self that the record doesn't
measure. On a seat too old to serve the sheet, the panel shows the old report
card instead.

### The toy dock

Under the 👶 tab, one icon per toybox toy, live whether or not the panel is
open (#249). This is where you play with self.

- **Each icon shows its toy:** a tower as a small stack (fallen blocks
  dimmed), a die its face (⚀…⚅), drift a gauge, burst ✦ (bright while its
  window is active, dim while quiet), fast and slow their value, body its
  count (self only).
- **A badge** names who changed it last (`self`, `world`, `M` for the seat's
  model, a person's initial) for a few seconds, then fades (no fade with
  *reduce motion*).
- **A dot** shows its health: green ok, amber empty reads, grey not read yet,
  red no getter.
- **Hover it, or tap it on a phone,** for a popover: its looks, its error
  earlier → later, the changes by self / world / people, the last change (by
  whom, how long ago), and the play actions: knock or rebuild per tower
  block, roll a die, nudge a drift or burst toy (body has none). The result
  shows in the popover (`✓ knock toy:tower block 3 → {"standing": false} (by
  alice)`). Esc closes it.
- **On a phone** the dock folds into one 🧸 button under the 👶 tab; tap it to
  expand (remembered per browser).

The icons are keyboard-reachable and labelled, and the field's labels keep
clear of the dock. It polls a light route every 3 s while the page is
visible (cached 2 s, read-only, JWT-gated, forwarded by the HA relay on GET):

```bash
curl -s http://127.0.0.1:4096/membrane/toybox/state
# {"toys": [{"name": "toy:tower", "kind": "tower", "reading": {"blocks": [true, true, false, false, false]},
#            "health": "ok", "looks": 48, "error_early": 0.1, "error_late": 0.34,
#            "changes": {"self": 5, "world": 0, "alice": 2}, "last": {"by": "alice", "what": "topple", "age_s": 4.2}}, ...]}
```

**Toys** in the sheet's inventory are `toy:` and `cradle:` things (listed even
without a getter, so a broken one shows red) and words (`w:`) that carry a
getter; a plain word is not a toy. Built-in toys from the toybox (see
[Toys](#toys)) are listed under any name, each with its `kind` (a script toy's
kind is `script`). The list is rebuilt from the store at most every 15 s. The
dock shows the toybox's toys only (the ones you can play with).

### The panel status

The full state is in the seat's
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
| `skill_rung`, `skill_pass`, `skill_measured` | #249: the rung that test belongs to, the line its skill has to reach (`null` for rung 4, whose bar moves), and whether anything feeds the test yet (before that, `skill` is a placeholder 0) |
| `slipping` | the top passed rung's test fails *now* (it stays passed on the record) |
| `ladder` | every rung: `passed`, `current`, `capped` (ready, held by the limit), `locked`, `unbuilt` |
| `world` | rung 3's sort of the things it reads: `silent` / `moving` / `still` / `intermittent` |
| `split` | rung 6's split: `body` (it predicts it near-perfectly) or `other` (it can't) |
| `meets` | rung 6's exchange: `pushes`, `other` (who it meets), `push_age`, `heard_age` (seconds since the other last answered) |
| `steer` | after rung 1: how close it lands to its aim (`err`) against not leaning into the current (`naive`), and `skill = 1 − err/naive` |
| `rung7` | rung 7 at the current other: `asked` (pushes that asked the model), `checked` (those whose reply check came), `paired` (those whose check carried the model's answer), `pair_rate` = paired / checked, `brier_model` and `brier_habit` (mean Brier error over the pairs the test reads; the model passes by beating habit's by 15%), `base_rate` (the all-time rate the model is told) and `recent_rate` (the share of the last 6 reply checks answered, same prior, also told). The 🌱 chooser's tooltip shows it as `rung 7: 5 of 8 asked pushes paired, Brier model 0.37 vs habit 0.39, base rate 0.55` |
| `toys` | #230: each toy (`toy:` / `cradle:` things, words with a getter) with `getter` (it has one), `reads` and `answered` (of its last 10 reaches), and `health`: `ok`, `empty` (no answer on its last reads), `unread`, or `no getter`; a built-in toy also carries `toy` (its toybox name) and, for a tower block, `block` (#244: the play buttons' target) |
| `off_field` | #244: of self's last 200 looks, `looks` (how many), `off` (how many landed on a thing with no live field point; `null` when the seat has no field) and `things` (those things, most looked-at first). The home view draws those looks as ghosts |
| `curiosity` | from rung 2 on: the (up to) three things whose prediction is improving fastest, best first, each with `progress` (its error over the 30 ordinary looks before the latest 30, minus its error over the latest 30) and `error` (the latest 30's mean Brier error). It is what self's curiosity reads when it chooses a reach (below); empty until a thing has 60 ordinary looks |

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
exchange is drawn too: the person self meets (`status().meets.other`, their
`chat:in:<person>`) is an amber orb labelled **you** (or their name), at their
own point on the field when it has one, else at the field's edge. A **beam**
goes out to it when self pushes a line, and when the person replies the beam
**flashes back** (green) from them to self.

To follow it, set the camera button **◎** to `self`: the camera glides with the
amoeba and chases it from behind its past positions. "self is not moving yet"
means it is off, or nobody has spoken yet.

**◎ self's framing** fits the camera to self's own neighbourhood rather than the
whole field. The distance comes from the bounding radius of the amoeba, its
last 4 steps, the points of the things in its trail of looks, and its aim, plus
a margin: it moves in close while self fiddles nearby and eases out when self
reaches far. The change is damped, so there are no jumps (with *reduce motion*
on, the camera steps straight to the new distance). A manual drag, pinch or
wheel keeps working and pauses the framing ("your framing") until you pick
◎ self again.

**👁 self's eyes** is the next camera mode after ◎ self. The camera sits at the
amoeba, which is hidden so it doesn't block the view, and looks toward what
self reaches for now. With no fresh look it faces self's aim, then the way it
is heading, then the field's centre. When self reaches for something new, the
view turns smoothly toward it (at once with *reduce motion*). The beam, the
trail and the labels stay, so you see the field as self sees it. A manual
orbit or zoom leaves the mode and goes back to ◎ self.

### The brain on the screen

While membrane home is open (and the tab is visible), it reads self's record
every 3 s (the 👶 panel's feed, `GET /membrane/self/recent`) and shows each new
line on the field (#237):

- **what it looks at now**: a **beam** from the amoeba to the thing it reached,
  with a label (`👁 w:burst ✓ changed`, `👁 the eye · unchanged`,
  `👁 dice · no answer`). Green means changed, grey unchanged, orange no
  answer. The beam fades after about 6 s. A thing goes where the field already
  places it: its drawn point, else the seat's `locate` (the record's `fid`,
  the seat's own field id for it, is tried first). If it is off screen, its
  label sits beside the amoeba with an arrow toward it. If the field has no
  place for it yet (#244), the look goes to a **ghost**: a hollow dashed ring
  a short fixed way from the amoeba, in a direction derived from a hash of
  the thing's id (so the same thing always ghosts to the same side), reached
  by a dashed beam and labelled "`<name>` (unplaced)". A ghost is never a
  filled dot, never on the trail and moves with the amoeba, so it can't be
  mistaken for a field position;
- **a trail** of the last 8 looks: a dot on each thing, joined in the order it
  looked, fading with age. Curiosity's wandering and patience's returns show
  up as a pattern;
- **a pulse** when rung 5 notices a shift: an orange ring pulses three times on the
  thing, captioned `noticed: w:prime changed its behaviour`;
- **its other acts**: a **wait** dims the amoeba briefly; a **level-up** echoes
  the exp bar's gold moment as a gold ring round the amoeba; a **push** is the
  exchange's beam (above);
- **the caption**: one line at the bottom of the view that narrates the newest
  record line in plain words and cites it (`#412 · reach`; the tooltip holds
  the line itself):
  - "self looked at w:burst — it changed (it expected 0.62)";
  - "self noticed w:prime stopped behaving like itself (it changed 0.8 of the
    time, now 0)";
  - "self said hello to you and asked the model if you'll answer";
  - "self waited — nothing was due";
  - "level up: rung 7 · understand an other".

  A number appears only when the line carries it. Things go by friendly names:
  the person self meets is "you" (or their name), `i` is "the eye", and a toy
  goes by its short name (`toy:dice` → "dice").

The labels round self are small chips on the theme's card colour, with the
label's own colour as their left edge, so they read on light and dark. They
never pile up. Each frame they are laid out so none overlaps another: self's
tag and the look keep their places first, and the marks (`wants`, `expects`,
`pushes to`, `pull`) step aside or hide when there is no room. They also stay
inside the visible field, which is the canvas minus the chat, the 👶 panel,
the top bar, the voice row and the caption. A thing behind a panel gets its
label at the panel's edge with an arrow toward it, and a ring whose thing sits
behind a panel is not drawn there. While the camera follows self (◎ self or
👁), self's tag shortens to `self · Lv 6 · 64%`; the exp bar carries the rest.

A fresh page starts the trail from the record's last looks and replays no
pulses. With *reduce motion* on, nothing pulses or wobbles: the rings hold
still for a few seconds and the beam stays steady. It is read-only: nothing
here posts to the seat.

**Every looked-at thing gets a field point** (#244). The first time self
reaches a thing that has no live point in the field (the eye `i`,
`field:vitals`, `chat:in:<person>`, a word like `w:rung`), the seat places it:
its summary, else its name, is embedded by the seat's embedder and becomes the
thing's seed in the field (`EntityField.place`). Its own mentions move it from
there as usual. It happens once per thing per run, never moves a thing that
already has a point, and does nothing when the seat has no field or no
embedder yet (the page shows a ghost meanwhile). `status().off_field` counts
the looks that still land off the field. At most 8 trail marks, 4 rings and 3 labels exist at
once, and the labels move about 8 times a second.

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
| **4 · patience** | fits each thing's rate of change in time over its last 30 looks (after a rung-5 shift, over the looks since it, with the old rate kept as a prior worth 3 looks, so a handful of looks can't swing it to never/always; #235); looks again once a change is 60% likely (waits up to 30 min); idles when nothing is due | things with a steady rhythm | **timed**: 30 timed looks; **news**: of its last 40, they find a change ≥ half the time, or 0.15 more often than its fixed-pace looks did (whichever is easier); **calibration**: its mean predicted chance is within 0.1 of the share that found news | keep getters' rhythms honest; don't make a thing change only when self looks |
| **5 · notice the world changed** | compares its last 3–5 looks at a thing with its long memory; a window far from it (> 0.4) and unlikely under it (binomial tail < 0.02) is a **shift** ("went still" / "moves again"), and it re-learns from there (its rate starts from the old one and the new looks lead after ~10) | a thing it already classes as moving | **looks**: 20 looks with noticing on; **audit**: membrane pauses the surest mover for 5 of self's looks (cap 1 h), then restarts it for 5 more (cap 3 h); self must flag "went still" during the pause and "moves again" after | nothing: the audit runs itself. Don't restart the seat mid-audit if you can help it |
| **6 · meet an other** | every 30 cycles pushes the line "self is here" to chat, and reads `chat:in:<person>`, which ticks each time the person sends a message | **a person who chats** with the seat, irregularly | **pushes**: 10; **heard**: 20 answered looks at the other; **split**: the other is classed **other** (its Brier skill < 0.9: self can't predict it perfectly). A partner who never answers is predicted perfectly, classes as *body*, and the rung stays shut | chat with the seat — normally, whenever you like. Your messages are the answers |
| **7 · understand an other** | at each push it asks the seat's model (`llm::call`, read-only, one consult per push) for one calibrated probability that the other will have spoken by its next look, telling it the all-time base rate (how often the other had answered by the reply check, with a small prior), the recent rate (the share of the last 6 reply checks answered, same prior), how long ago they last spoke, and the recent reply checks; it holds off looking at the other for 2 minutes (up to 3 while the model's answer is still in flight), then checks for the reply, and that look pairs the model's prediction with its own habit | the seat's model reachable for background calls; the other answering its pushes | **looks**: 20 paired looks (one per push: habit and model on the reply check); **skill**: the model removes ≥ 15% of habit's Brier error over them | answer self's 🌱 lines (or just chat); keep the seat's model up |

At the default pace, rung 6's ten pushes alone take ~50 minutes; rungs 2–5
usually take hours, and the audits run on their own schedule.

### How self chooses what to read

From rung 2 on, each cycle self reads one thing. The rung it has reached sets
the **base**: at rung 2 any thing with a getter, evenly; at rung 3 a silent
thing with weight 0.05; at rung 4 only the things whose next change has become
60% likely (or a silent one now and then), and nothing when none is due.

On top of that base, **curiosity** ([#212](https://github.com/chedim/membrane/issues/212))
tilts the choice toward the things it is **learning fastest**: where its
prediction error has been falling. It works the frontier, not what it is
best at. A thing it has mastered (error flat and low) and a thing it can't
learn (error flat and high) both drop to a tenth of the weight. They are still
retried now and then, in case the world changed. A thing too new to judge (fewer than 60
ordinary looks) keeps its full weight, and when nothing is improving anywhere
the base runs untilted. Curiosity never makes a thing due at rung 4 and never
lifts a silent one: it only reweights what the base already allows.

Three rules (#161) keep curiosity from breaking the measurement:

- **The rise, not the level.** It reads the change in the error, never the
  error itself, so it can't earn anything by staring at a thing that never
  changes.
- **The audit stays blind.** Only ordinary looks count. Looks inside a rung-2
  or rung-5 audit (a paused source, and its restart) are left out, so self
  can't learn when it is being audited.
- **Seeing never drives the loop.** Curiosity is worked out from the record
  only at the moment self chooses. It is not stored, never wakes a cycle, and
  is never an answer (not on the record, not in `tempo.json`).

`curiosity` in the panel status shows the numbers it reads.

**Not built yet** (the chooser lists them, disabled):

| rung | skill | the test the design gives it |
|---|---|---|
| 8 · recognize itself | shown its own trace and another's | it predicts its own better (the mirror test); then the name ceremony, agreed with the operator |
| 9 · change itself | `cliff::define` on its own methods | a revised method predicts better than the version it replaced, and reverts on strike-out |
| 10 · speak | its voice | every line cites its ledger; no unbacked first-person claims |
| 11 · invention ([#196](https://github.com/chedim/membrane/issues/196)) | the field fills a gap with a new entity in a new state | future; a design thread, not on the chooser |

## Toys

Toys are the apparatus the ladder is measured with, so they ship with the seat
as tested code (`membrane/toybox.py`). You and the seat's model only switch
them on or off and set their parameters. Nobody writes toy code.

### Why they are built in

Before #239 the seat's model wrote every toy as an entity script, and nearly
every one broke in a new way. One kept its state in `/tmp`. The tower's relay
misparsed block ids, so 13 of self's reaches did nothing. One getter dumped the
whole entity, position vector included. A die's getter clobbered its own tick
state. A probe was never removed, and some scripts were refused at the length
cap. A broken toy doesn't only waste self's looks: it makes the rung tests
measure the bug.

### What every toy guarantees

- **An entity with a stub getter.** The getter is two lines that call into
  `toybox.py`. A toy with a clock of its own also has a stub tick on the
  thinker's heartbeat. Self reaches it like anything else that has a getter.
- **A minimal reading.** Only the toy's own state (`{"value": 42}`,
  `{"face": 3}`, `{"standing": false}`). Never the seed, the log or who caused
  what.
- **State in the data folder.** `<data dir>/toybox/<name>.json` holds the
  state (atomic writes) and `<name>.log.jsonl` the change log (append-only).
  Both survive a restart.
- **A seeded RNG.** The `seed` parameter defaults to a number derived from the
  name, and draw *k* is always the same for the same seed, so a run replays.
- **A contingency flag on every change.** Each line of the log says who caused
  it, with the time:
  - `by: self`: self's own look or reach;
  - `by: world`: the toy's own clock or randomness;
  - `by: model`: the seat's model, through `toybox.poke` (#244);
  - `by: <person>`: a person playing (#244): `operator` from the CLI, or the
    page's person (their name, or their token's subject in JWT mode). A
    person whose name is `self` or `world` is written `person:self` /
    `person:world`, so the toy's own tags keep their meaning.

  The getter knows the reader because `entity_read_back` hands it its `by`
  (`state.extra.by`: `self`, `model` or `field`). This is the ground truth
  rung 6's body/other split can be checked against. Self never reads it: the
  reading stays minimal. It goes on self's **record** instead: each reach of
  a toy carries `by`, the distinct tags of every change to that thing (that
  block, for a tower block) since self's previous look at it, through this
  look's own act. A person's knock that self finds on its next look reads
  `"answer": "changed", "by": ["alice"]`, a change self didn't cause.
- **A spot in the field.** When a toy is added (or migrated), each of its
  entities is placed in the field from its summary (#244), so the home view's
  beams and trails have somewhere to go. With no embedder the toy still comes;
  it gets its spot on self's first look once the embedder is up.

### The kit

| kind | changes when | parameters (defaults) | for |
|---|---|---|---|
| `fast` | on its own clock, faster than self looks, so every look finds it changed | `every` (5 s) | rungs 3–5, curiosity |
| `slow` | once a period | `period` (3600 s) | patience (rung 4) |
| `burst` | every `every` s inside active windows; still the rest of the time | `window` (600 s), `active` (0.35), `every` (45 s) | rungs 3–5 |
| `drift` | a ±`step` random walk at random intervals | `min_s` (45), `max_s` (600), `step` (5), `lo` (0), `hi` (100) | curiosity, rung 5 |
| `die` | memoryless: probability `p` per second, on its own clock, never on a read | `p` (1/60), `faces` (6) | rungs 3–5 |
| `body` | only when **self** looks at it | none | the body/other test case (rung 6) |
| `tower` | only when **self** reaches a block: a standing block topples with every block above it, and a fallen block is lifted back | `n` (5) | self's first act on the world |

Every kind also takes `seed`. A tower is one entity for the whole (its reading
counts the standing blocks) plus one per block, `<name>:b1` (the bottom)
through `<name>:b<n>`. Each block remembers who toppled it and when. Of the
reads, only self's reaches change the tower: a read by the model or the field
leaves it as it is. Others change it by playing (below).

`body` is the one toy built to change exactly when self looks, which "Don't
game the getters" below warns against. It is deliberate, labelled, and its log
says `by: self`, so it is a known test case, not a fake mark.

### Adding one

From the CLI (the project's data dir):

```bash
membrane toybox list
membrane toybox add tower --param n=5
membrane toybox add die --name toy:d6 --param p=0.02 --param seed=7
membrane toybox reset toy:tower      # same params and seed; the old run is archived
membrane toybox remove toy:d6        # the entities go; the history is archived
```

`add` and `remove` change the entity store, so the CLI refuses them while the
seat is running. Use the seat's model then: the `toybox` object answers
`add(spec)`, `list()`, `remove(name)` and `reset(name)`, for example
`exec(action='call', object='toybox', method='add', args={'spec': 'tower n=5'})`.
The default name is `toy:<kind>`. A removed or reset toy's state and log are
moved to `toybox/archive/`, never deleted.

### Playing with self

An other acting in self's world is what rungs 6 and 7 are about, so you and
the seat's model can play with the toys (#244). Every change is tagged with
who made it.

| toy | action | what it does |
|---|---|---|
| `tower` | `knock <k>` | block k and every standing block above it fall |
| `tower` | `rebuild <k>` | block k alone stands again |
| `die` | `roll` | a fresh face (never the same one) |
| `drift` | `nudge [up\|down]` | one step (a coin picks the way when none is given); it bounces off a wall |
| `burst` | `nudge` | a fresh value |
| `body` | none | self's alone: the control. A poke is refused |

`fast` and `slow` take no pokes either: their clock is the point. A poke that
would change nothing (knocking a fallen block, rebuilding a standing one) is
refused, so every logged poke is a real change. A tower block can be named
directly: `toy:tower:b3 knock` is `toy:tower knock 3`.

Three ways to play:

```bash
membrane toybox poke toy:tower knock 3          # by: operator
membrane toybox poke toy:die roll --as alice    # by: alice
```

- **The CLI** tags `operator` by default (the seat's `''` person: what
  `chat_io.recent_person` means by "the operator"), or `--as <name>`. A poke
  only touches the toy's own files, under the toy's lock, so it works while
  the seat runs; self sees it on its next look.
- **The seat's model** calls `toybox.poke(toy, action)`, for example
  `exec(action='call', object='toybox', method='poke', args={'toy': 'toy:tower', 'action': 'knock 3'})`.
  A method call doesn't say who called it, and the toybox object is the
  model's surface (self's own reaches go through the getter), so every poke
  made there is tagged `model`.
- **The toy dock's play actions** (#249; the report card's buttons before
  it): each toy's popover under the 👶 tab offers knock / rebuild on each
  tower block, roll on a die, nudge on a drift or burst toy. They post to `POST /membrane/toybox/poke`
  `{"toy": "toy:tower", "action": "knock", "arg": 3}`, the page's only write to self's world.
  The route takes toybox actions only and sits behind the JWT gate like every
  route. The seat tags the poke with the page's person: the token's subject in
  JWT mode, else the standalone page's speaker line (the name you typed),
  else the HA user (the HA relay stamps `person`), else `operator`. A `person`
  the page puts in the body is ignored whenever any of those exists.

### Migrating the model-written toys

```bash
membrane stop
membrane toybox migrate --dry-run    # lists each script toy and what it would become
membrane toybox migrate --yes        # replaces them (each old doc is backed up first)
```

`w:fast`, `w:slow`, `w:burst`, `w:drift`, `w:body` and `toy:die` become the
built-in toy of that kind under the same name. `toy:tower` becomes a built-in
tower with as many blocks as it had. Its helpers (`toy:tower:reach`, the
`knock`/`raise` methods) are retired. Every doc that is replaced or retired is
first written to `toybox/migrated/`. Script toys with no built-in match are
listed and left as they are. Without `--yes` nothing changes. The names stay
the same, so self's record of them continues. Their behaviour does change,
though, and rung 5 may notice that shift.

## Helping it grow

- **Give it toys from the toybox.** `membrane toybox add <kind>` (above) gives
  it apparatus that is correct, replayable and honest about causation.
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

That was [#208](https://github.com/chedim/membrane/issues/208), fixed in
#209: rungs 6–7 were hardcoded to `chat:out:operator` / `chat:in:operator`, so
on a seat whose person has a name nobody ever answered. Self now meets **the
most recently active person** (the owner of the newest chat), follows when
that changes, and falls back to `operator` only on a seat with no people. Its
line starts with 🌱 and the chat shows it as **🌱 self**, so you can tell it
from the seat and answer it. Rungs 6–7 count only the current other, so a seat
that "passed" rung 6 against the empty slot shows it **slipping** until the
real person earns it (10 pushes, 20 answered looks, classed `other`). The
panel's `meets.other` names the person once self has pushed to them — at most
one meet (~5 minutes) after a restart.

**Rung 7 waits on `looks` for a long time.** It earns at most one paired
look per push, and self pushes once every 30 cycles (~5 minutes at the default
pace), so 20 pairs take at least ~100 minutes of running. At each push self
asks the model whether the other will have spoken by its next look, waits 2
minutes, then checks for the reply: that look is the pair. A push yields no
pair when the model was not asked (the push line's `asked` is `false`: the
hourly `llm::call` budget is spent, the model is switched off, or the seat is
paused), when the model's answer had not landed by the reply check, or when
the check didn't happen within 10 minutes of the push. The pending question
lives in the record, so a restart doesn't lose it. A working background model
and an other who sometimes answers are what move it; an other who never
answers can't be predicted *better* than habit, which already says "silent".

**Rung 7's model is overconfident, or its asks don't pair (#217).** Read
`rung7` in the panel (or the 🌱 tooltip). On the live seat (2026-10-18) 7
pushes asked and only 4 paired, and the model said 0.85–0.99 "will have
spoken" to reply checks that went 3 of 7: its Brier error was worse than
habit's ~0.2 guesses. Two causes, both fixed:

- *The question gave no base rate.* Its history listed pre-#216 looks made
  5–25 minutes after a push ("they had spoken" ×14), another horizon, and the
  model read it as "they always answer". Now the prompt carries only reply
  checks, the **base rate** (`k of n`, Jeffreys prior: 0.5 before any check),
  and asks for one calibrated probability, saying a confident miss costs.
- *Three answers weren't answers.* They landed 28–75 s after the push (the
  question rides a full read-only loop turn: `mistral:7b` routed up to
  `glm-5.3-flash:cloud`, 2.4–74 s per turn, 8–10 s push → answer when on
  task), well before the 2-minute check, but were prose ("Membrane is a tool
  …", "Good day! I am Memo …") with no number. The prompt now says no tools
  and `VERDICT: <probability>`, and the parser takes a decimal in 0..1 or a
  percent (after `VERDICT` when there is one), never a stray `#161` or
  `120s`.

The `[bg] llm call failed (timed out)` lines in the seat's log are the
thinker's background lane (local `mistral:7b`, 120 s timeout), not self's
question. If an answer is still in flight at the reply check, self now waits
up to 60 s more (`RUNG7_GRACE_S`) before looking, and pairs only an answer
that landed after the push and by the look; a `pair_rate` well under 1 with
`checked` growing means the model's answers aren't parseable or arrive later
than 3 minutes — look at the `field-call: chat:in:<person>` sessions.

**Rung 7's skill is negative though the pairs are in (#246).** On the live
seat (2026-10-19) rung 7 had its 20 pairs and skill −0.34. The question now
carries the **recent** rate next to the **all-time** one (labelled so), how
long ago the other last spoke (chat:in's `heard_ts`, or self's last look that
found them changed — a time, never the words), and a sentence that rhythms
change; `recent_rate` is in `rung7`. Only the input changed: 20 pairs, the
15% margin, the pairing and the parsing are as before. Measured on a copy of
that record (`experiments/rung7_recent/`): this person's reply by the
2-minute check is close to a fair coin with no runs, so the recent rate is
not what was missing (it is a hair better than the all-time one over the
last 20 checks, worse over all 77 and over the pairs rung 7 read). Habit is
beatable there — it scores worse than always saying 0.5 — but only by a
model that answers near the rates it is told; the live model said 0.58–1.0
to checks that went 8 of 20. If `brier_model` stays above `brier_habit`
with `base_rate` / `recent_rate` near 0.5, the model's lean is the problem,
not its information.

**`held`.** Three cycles failed in a row (the record's `error` lines say why).
Fix the cause, then re-choose the limit to clear it.

**Want to start over?** Off keeps everything; there is no reset switch. Moving
`<data dir>/self/` aside with the seat stopped gives a newborn at the next
switch-on, and the old record stays on disk for reading.
