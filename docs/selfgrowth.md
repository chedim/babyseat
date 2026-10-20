# Growing self — the operator's guide to the 0.7 skill tree

`self` is the seed of the evolving self in
[#161](https://github.com/chedim/membrane/issues/161): a small loop that learns
its world one skill at a time. The operator named it **Blurp** (2026-10-19,
[#277](https://github.com/chedim/membrane/issues/277)): the page shows that name
wherever it shows self to you (the 👶 panel and its sheet, the toy dock, the
activity lines, the amoeba's tag, ◎ and 👁 in the camera menu); its ids, its
record and this guide keep `self`. The name is **per seat** (#283): Blurp is the
default, and each seat can name its own so a house with several seats tells
them apart -- `membrane self name Pip` (or `membrane self name` to show it,
`--reset` for Blurp again), POST `/membrane/seat/control {"self_name": "Pip"}`,
or `MEMBRANE_SELF_NAME` over both; it is stored as `self_name` in the seat's
`settings.json`, trimmed, 1-24 printable characters. This page tells you how to wake it, how to read
its progress, what each rung asks of its world, and how to help it climb. The
mechanics, rung by rung, are in [background-mind.md](background-mind.md#the-self-loop-161);
the code is `membrane/self_loop.py`.

One rule runs through all of it: **every rung is measured, never claimed.** A
rung opens when its test passes on self's own record, and nothing anyone *says*
about self (you, the seat's LLM, self's own lines) moves the bar.

## The skill tree (#295)

The rungs used to be a strict ladder: rung N's test ran only after rung N-1
passed. Since #295 they are **achievements** in a skill tree: each has a
stable id, a branch and **real prerequisites** -- an edge only where its test
reads data or machinery that another achievement's pass switches on. A test
runs whenever its prerequisites are earned, so siblings unlock **in any
order** (the live seat could earn *expect a reply* before *time its turn*).
Each test keeps its own bar, exactly as measured before; there is no new hold
rule.

| id | achievement (its test's rung) | branch | prerequisites | why that edge | a child, by what the test measures |
|---|---|---|---|---|---|
| `feel` | feel itself (1) | self | — | a root: it reads only its own moves and the current's push | newborn: tells its own touch from another's (Rochat & Hespos) |
| `reach` | first reach (2) | world | — | a root: its looks predict a thing from the thing's own tempo; nothing of feel (the current, the steering) is read | ~2-3 mo: its kicks move a mobile (Rovee-Collier) |
| `kinds` | tell things apart (3) | world | reach | its looks are reach's looks, and one stage is reach's skill still holding on a moving thing | ~3-4 mo: perceptual categories (Quinn & Eimas) |
| `patience` | patience (4) | world | kinds | its timed looks skip the things sorted silent; the sort is switched on by that pass | ~3.5 mo: anticipatory looking (Haith) |
| `shift` | notice the world changed (5) | world | reach | its noticing reads the looks and each thing's long memory (the tempo); its audit pauses a moving thing self has looked at. It does not read patience's rates (patience reads its shifts) | ~3.5-5 mo: violation of expectation (Baillargeon) |
| `meet` | meet an other (6) | others | reach | its answered looks at the other are reach's looks, and the self/other split is reach's own prediction skill | ~3-5 mo: self- vs social contingency (Bahrick & Watson) |
| `turns` | time its turn (7) | others | meet | it times its pushes to the other meet found, through meet's channel, scored by the reply check | ~3-5 mo: times its turn in a proto-conversation (Bateson; Trevarthen; Jaffe et al.) |
| `expect` | expect a reply (8) | others | meet | it predicts whether meet's other replies to a push, at the reply check; it does not need time its turn's learned timing (a push by the timer is checked and predicted the same) | ~2-3 mo: expects a response (the still-face, Tronick 1978) |
| `mirror` | recognize itself (9, #312) | later | feel, meet | feel: what the mirror shows is its own moves, the body whose steering feel's pass switches on; meet: it labels the glass and the stranger with meet's self/other split (Brier-style skill ≥ 0.9: my body) | ~18 mo: the mirror test (Amsterdam 1972) |
| `change` | change itself | later, not built | mirror | planned | ~4 y: metacognition |
| `speak` | speak (11, #311) | later | turns, expect | its words take turns: its timing is time its turn's policy, and one stage is that skill's contingency; its outcome is expect a reply's reply check after each push | ~12 mo: first referential words, naming a thing to an other (Bates, Camaioni & Volterra 1975). Until #311 the tree said ~4 y, autobiographical narrative; the test measures first words, not a story |
| `invent` | invent | later, not built | change, speak | planned | ~5 y: games with its own rules |

- **Earning** appends `{"kind": "achieve", "id": "expect", "rung": 8, "ts": ...,
  "skill": ...}` to the record. The record stays append-only: the old `rung`
  lines count as their achievements (rung 1 = `feel` ... rung 8 = `expect`),
  a `rung_void` still voids, and a pass above rung 6 from before the ladder
  flip still counts for neither (#258) -- the voided old rung 7 stays void.
- **The level** is the number of achievements earned (sleep and wake is not
  one), accepted or not. `rung_name` is the latest earned achievement's name;
  `next` lists the open ones (built, unearned, every prerequisite earned and
  accepted).
- **An earned achievement opens its children only once you accept it**
  (#299, [Accepting what it earns](#accepting-what-it-earns-299)). Until then
  it counts for the level and opens nothing.
- **Practice follows the tree too.** Each achievement's machinery runs while
  self is awake and its prerequisites are earned and accepted: reaching from
  birth (a root), the sort after first reach, noticing after first reach, the
  pushes to the other after first reach, and the reply checks after each push
  while either *time its turn* or *expect a reply* may be practised. The
  ball's turns are time its turn's practice. Acts with outside effects (the
  pushes, the ball, the reply checks, the toy unlocks) stay behind their
  achievement's prerequisites and your accept of them.
- **The names** (#300) say what each test measures: `turns` is *time its
  turn* (it was "take turns") and `expect` is *expect a reply* (it was
  "understand an other": its test checks that self expects the other's reply
  better than the running rate, a 2-3-month ability -- the still-face upset --
  not the nine-month reading of intentions). The ids, the record's lines and
  the rung numbers are unchanged, so every old line keeps counting.
- **The data** for the 👶 panel's tree is `tree` (and `level`) in
  `GET /membrane/self/sheet` and in the dock (`GET /membrane/toybox/state`):
  one row per achievement, `{id, name, branch, prereqs, built, state
  (locked / open / earned / unbuilt), accepted (#299), earned_at, skill, bar,
  waiting_on, child_age, child_ability, why_prereqs}`, beside `accepted` (the
  ids you accepted) and `awaiting` (earned, not accepted yet).

## What self is, and what it is not

- **It is** `membrane::self`, a looped object (named Blurp) with three methods:
  `expect` says beforehand what will happen, `try` acts, `notice` sees what
  actually happened. The gap between `expect` and `notice` is what it learns
  from. Its acts are small and safe, field-only: move its own position, read a
  thing (fire its getter), push a line to chat (rung 6), roll the shared ball
  to you (rung 7 on, #264). **There is no shell** in its repertoire, and
  adding one is not on the ladder.
- **It is not the LLM.** The seat's model never speaks *as* self and can't call
  its methods (they refuse any caller but self's own loop). The model talks
  *to* it and, at rung 8, is an advisor self consults: self makes the
  prediction and weighs the model's guess by the model's own track record
  (#254); rung 8 grades self's prediction, never the model's. Rung 7 (take
  turns) has no model at all: self learns *when* to take its turn with the
  ball.
- **It is not a self-model** to be scored. It is a loop that builds up a body
  of measured predictions about its world.
- **It doesn't sleep.** Off is not sleep: nothing runs. Why it hasn't needed sleep yet, and what would change that: [sleep.md](sleep.md).
- **It is not `seat:self`.** That is only the speaker tag: every line the seat
  says moves it. `self` is a separate entity that only its own acts move;
  mentions of it in the conversation never do.
- **Its memory starts at birth** (the first switch-on). Everything the seat did
  before is the field it grows in, not "its" past.

## Waking it

### Sleep and wake

Self is **off by default**. The 💤 / 🌱 toggle in the 👶 panel's header on
membrane home (and in the chat's side menu) wakes it and puts it back to sleep:

| state | what happens |
|---|---|
| 💤 asleep | nothing runs, **nothing is erased** (record, model, tempo stay) |
| 🌱 awake | self cycles, practises every open skill and earns what its tests pass |

The same switch over HTTP is `{"self": "on" | "off" | "watch"}`. **`watch`
currently runs exactly like `on`**; it exists for the nanny the design
calls for, which is not built yet, and the viewer only tags the amoeba
"watched". The switch is the operator's alone (`POST /membrane/seat/control`:
localhost, the seat token, or an HA admin through the relay). The seat's LLM
can read self's state; it can't change it.

The ladder flipped in #258: rung 7 is **take turns** (serve and return) and
**understand an other** moved up to rung 8, unchanged (since #300 they are
named for what their tests measure: *time its turn* and *expect a reply*). A
seat at rung 6 reads rung 6 after the update and works on the new rung 7.

### Accepting what it earns (#299)

Until #299 a 🌱 **growth limit** chooser decided how far self might grow. It
is gone: **earning stays automatic, and growing on is yours to allow.**

- **Earning.** A test passes on the record -> an `achieve` line, the level
  counts it, membrane home celebrates it. Nobody has to do anything.
- **Accepting.** An earned achievement opens what it leads to only once you
  **accept** it. A skill is *open* -- its practice runs, its acts reach the
  world, its test runs -- when every prerequisite is earned **and** accepted;
  the roots (feel itself, first reach) are open from birth. An earned,
  unaccepted achievement counts for the level and opens **nothing**: no
  practice, no chat pushes, no ball rolls, no reply checks, no toys. Accepting
  grants nothing either: what it opens must still earn its own test.
- **Where.** The celebration on membrane home asks
  "🏅 Blurp earned time its turn -- accept, and open what it leads to?" with
  **🏅 Accept** and **Later** buttons (the question stays until you answer it;
  *Later* asks again on the next visit). The Skill tree tab marks an earned,
  unaccepted skill **🎁 earned · awaiting your accept** (dashed gold), and its
  card has the **🏅 Accept** button. The browser notification for a new
  achievement says it awaits your accept.
- **Over HTTP or the CLI:**

```bash
curl -s -X POST http://127.0.0.1:4096/membrane/seat/control \
  -H 'content-type: application/json' -d '{"accept": "turns"}'
# {"ok": true, "applied": {"accept": {"ok": true, "id": "turns", "name": "time its turn", "already": false}}, "state": {...}}
membrane self accept turns            # the same, through the running seat (--url, or $MEMBRANE_SERVE_URL; default :4096)
```

  (In [JWT mode](auth.md) add your token; off-box, the seat token.) An unknown
  id, or one self has not earned, is refused with the reason
  (`"'recognize itself' is not earned yet: ..."`); accepting twice is a no-op
  (`"already": true`) and writes nothing.
- **The record says when it grew and when it was let grow.** Each accept is
  one line, `{"kind": "accept", "id": "turns", "ts": ..., "by": "operator"}`;
  `by` is who called (`operator` on localhost or the seat token, `ha:<name>`
  for a Home Assistant admin through the relay, `user:<sub>` for a JWT
  person, `ui:<name>` for the standalone page's speaker) -- never a secret.
- **The migration.** Every achievement earned before this code first ran
  counts as accepted. Nothing is written per achievement: the first boot
  writes one line, `{"kind": "accept_migrated", "ids": [...], "ts": ...}`,
  naming them (a seat never switched on writes it at birth, empty). A pass
  that a `rung_void` voids and that is earned again needs a new accept.
  On the live seat (Lv 7: feel, reach, kinds, patience, shift, meet, expect)
  all seven count as accepted, so time its turn stays open, as it was.
- **`self_limit` is deprecated** but still accepted, so Home Assistant and
  scripts don't break: `0` / `"off"` puts self to sleep; a number (the old
  ladder's rungs 1..N), an achievement id (it and its prerequisites) or
  `"all"` wakes it and **bulk-accepts** every earned achievement in that
  closure (one accept line each). It pre-accepts nothing: a pass after it
  still waits for its accept. A growth limit saved in `mode.json` by an
  older seat is ignored.

### The runaway guard

Each cycle's own change wakes the next, at most every 10 s
(`MEMBRANE_SELF_PACE_S`, floor 1 s) and at most 30 a minute — so about 360
cycles an hour at the default pace. Three failed cycles in a row **hold** the
loop: the status reads "🥀 held: switch it off and on to clear", and any
toggle clears it. **Pausing the seat pauses self** too.

### Where its state lives, and what a restart keeps

Everything is in `<data dir>/self/` (`.membrane/self/` in a seat's directory):

| file | what | after a restart |
|---|---|---|
| `mode.json` | the mode (off / on / watch) | kept: a seat wakes with the self it was given, and only that |
| `record.jsonl` | append-only, never truncated, from birth: every cycle, reach, push, wait, shift, audit, error, each achievement earned and each accept (#299) | kept: earned stays earned, accepted stays accepted |
| `model.json` | rung 1's learned model of the current that pushes it | kept |
| `tempo.json` | per thing it reads: how often it had changed when self looked | kept |

Its toys' state lives next door, in `<data dir>/toybox/` (see [Toys](#toys)),
and is kept across a restart too.

What a restart drops: a hold (cleared), its current aim (it picks a new one),
and an audit in flight (the paused getter resumes; the audit is started again
later). On a fresh boot nobody has spoken yet, so there is no current to feel.
Self keeps cycling anyway (#282): in a still world the current is where it
stands, so nothing pushes it, and it goes on looking, playing and taking turns
with the ball. Those cycle lines carry `still: true` (`change: "still"`), teach
its model of the current nothing, and are skipped by every stat that reads
push / gap / naive (rung 1 and the steer stat), so a still spell neither passes
nor fails a rung. Before rung 1 is passed the status says it waits on "the
world", since feeling a push needs one. Playing counts as speaking: a person's
poke on a toy pulls the current toward that toy under their own key
(`toy:<person>`), one event's worth, exactly as a chat line does
(`efield.pull_current`); self's rolls, the world's changes and the model's
pokes never pull it.

## Watching it grow

Everything below is in the seat itself: the exp bar, the 👶 panel laid out as
a character sheet, and the toy dock (#249). The external "Self's record"
dashboard that used to be rebuilt hourly by a script outside the seat is
retired; its numbers (working hours, the bug windows, the growth curve
against the fastest possible and a child) are computed by the seat now.

### The exp bar

In the top bar, self's progress reads like a game's experience bar
(#230):

```
Lv 6 · meet an other  [██████░░░░] 64%
→ 7 · time its turn · waiting on: pushes
```

- **the level** is the number of achievements earned (#295), with the latest one's name;
- **the bar** fills toward the next rung from `bar` (the staged test, below),
  with its percent; the caption under it names the next rung and what the bar
  is `waiting on`;
- **slipping** tints the bar amber (the top passed rung's test fails now);
- **held** greys it ("switch it off and on to clear");
- **accept?** (#299): nothing open is running and an earned achievement
  waits for your accept, so the bar is hatched and the caption says so;
- **asleep** shows only `🌱 asleep`;
- **a new rung** gets a short level-up moment: the level turns gold, the bar
  glows once, and the caption reads `⬆ level up` for a few seconds. With
  *reduce motion* on, the gold and the caption stay and nothing pulses.

**An achievement celebrated** (#289; per achievement since #295): on membrane
home each earned achievement is unmissable -- about 3 s over the field (the
amoeba glows and hops, a burst of particles in its mint and gold, the caption
"Blurp earned understand an other!"), a short synthesized chime (WebAudio, no
asset files) and a browser notification ("Blurp earned understand an other",
one per seat and achievement: tagged). With *reduce
motion* on it is a still banner. It never blocks the page (a click anywhere
ends it). The **🔔 / 🔕** in the top bar mutes the chime and the notifications,
remembered per viewer; its first tap asks for notification permission (the
browser wants a click for that) and later taps mute. The chime waits for the
page's first click or key (autoplay rules). Notifications need a secure
context -- https, or localhost: a laptop on `http://<lan-ip>:4096` gets the
celebration and the chime but no notifications, and the 🔔's tooltip says so.

**Once per achievement per viewer**: the page compares the seat's earned
list (status's `earned`, which a voided pass never enters) with the ids this
browser has celebrated (localStorage `membrane.home.achieve.<seat>.<name>`).
Each id earned while the page is open celebrates; those earned while away
celebrate once on the next visit (one overlay naming them, a notification
each); a reload never re-fires; a voided pass never celebrates (and leaves
the memory quietly, so earning it again is news); a browser's first visit
only remembers what is already earned. A browser that celebrated rungs under
the old per-rung rule reads that memory once: rungs 1..N it celebrated count
as seen, so the first visit after the upgrade never re-celebrates them (a
seat without `earned` still gets the per-rung rule). A hidden tab notifies at once and saves the
overlay for when it is looked at.

**The skill at a glance** (#249): under the bar, the number the bar's test
reads against the line it has to beat, e.g. `skill +0.03 / +0.05` at rung 7
(the reply rate its timing gains over a timing-blind push, which must reach
+0.05; #258) or `skill −0.00 / +0.08` at rung 8 (self's Brier skill over the
running rate, which must reach +0.08; #254). Before anything feeds the test
it reads `skill not measured yet / +0.05`, never a made-up 0.
Rung 4's headline (the share of timed looks that find news) has a moving bar,
so it shows the skill alone.

The bar's tooltip still carries the long form (mode, cycles, rung 7's
window and payoff, rung 8's pairs). On a phone the caption row is hidden to keep the top bar short.

### The 👶 panel: self's character sheet

A 👶 tab sits at the left edge of the home view (clear of a phone's notch).
It opens self's side panel: a column beside the field on a wide screen, a
full-screen overlay on a narrow one. Open or closed is remembered per browser.
Esc or × closes it.

The panel is laid out like an RPG character sheet: its structure, not its
styling (membrane's own look, light and dark). Each section folds; the
nameplate, Attributes and the Quest log start open, and which sections you
open or close is remembered per browser.

- **The nameplate:** the name (**Blurp** by default; the seat carries it, each seat its own -- #283),
  **Lv N · <the latest skill it earned>** (#300: the level is the number of
  skills earned; never a rung), what it is **learning** now, the live exp bar,
  its age (hours since it woke) and its **working hours**.
- **Attributes** (#300): a few **traits of self as a whole**, each measured
  from its own record -- never claimed, never a skill's test score, and they
  **gate nothing** (no pass line; nothing reads them to decide). Each shows
  its value, one line on what it measures, the numbers it came from, and a
  sparkline of the last four hours (the same formula over the record as it
  stood every half hour; `traits()` in `membrane/self_sheet.py`). An
  *ordinary look* is an answered look with a prediction, outside every blind
  audit; "recent" is its last 500 looks (about an hour of working time).

  | trait | the formula | reads as |
  |---|---|---|
  | Curiosity | of its last 500 ordinary looks at things that have a learning-progress reading (`self_loop._progress`: a thing's error over its 30 looks before the latest 30, minus over the latest 30), the share at things whose progress is above curiosity's floor (`CURIO_MIN`, 0.005). A thing too new for a reading (under 60 looks) is left out | how much of its attention goes where it is still getting better |
  | Insight | 1 − Brier(p) ÷ Brier(base) over its last 500 ordinary looks, the base being the share of those looks that found a change (the best constant guess); the detail adds the calibration error (ECE, ten bins of p) | 0: no better than knowing the overall rate; 1: every outcome called with certainty; negative: worse than the rate |
  | Adaptability | over its last 15 noticed shifts (rung 5's `shift` lines): in the window of looks at that thing that showed the change (the shift's `looks`, since its previous shift), the looks from the first miss (a look its prediction put on the wrong side of even odds) to the shift, inclusive -- the whole window when none missed; the median | how many looks it takes to notice the world changed and re-learn (fewer is better; about 3) |
  | Focus | 1 − H ÷ log K: H the entropy of which thing each of its last 500 looks went to, K the things it has ever looked at; the detail adds the repeat rate (the same thing as the look before) | 1: every look at one thing; 0: spread evenly over everything it knows |
  | Sociability | pushes ÷ (pushes + looks) over its last 500 acts -- a push is an act aimed at an other (a line sent, the ball rolled); the detail adds how often they answer: the share of its last 50 reply checks that found a reply | how much of what it does is aimed at others, and how often they answer |

  On a copy of the live record (2026-10-19, about 26 working hours):
  Curiosity 18% (3 of the 12 things it looked at still being learned),
  Insight +0.51 (Brier 0.077 against the rate's 0.156; ECE 0.03),
  Adaptability 3 looks, Focus 0.36 (21 of 25 things), Sociability 4% (18 of
  500 acts; 22% of its last 50 reply checks answered). A trait nothing
  measures yet says "not measured yet", never 0.
- **Learning now** (#300; the section that was the Quest log): **only the
  skills it is learning** -- open in the tree (built, unearned, prerequisites
  earned and accepted) -- side by side; a skill that only your accept holds
  back (#299) is listed too, marked 🎁 "waiting on your accept of <name>: it
  isn't practised until you accept it". Each one with its
  bar, its test's staged criteria with their counts (time its turn, #264:
  `37/100 turns with the ball with a reply check (you roll it back) · turns
  in its window 0.41 vs chance 0.22 (z 2.4 → 3.0, +0.10) · replies gained
  over timing-blind +0.08 (t 1.9 → 5.0, +0.05)`; expect a reply: `100/100
  reply checks ✓ · skill vs running rate −0.00 → +0.08`), what it is waiting
  on (the first stage not met), and **its skill value at a glance**: the
  number big, the line it has to beat, its change over the last hour and a
  sparkline of the last four hours (the same test run over the record as it
  stood every half hour). Expect a reply also says `restarted <date>: was
  grading the LLM, now grades self` once the fixed scorer started counting
  (#254). **Earned skills leave the card** -- they live in the Skill tree tab
  and its Achievements list -- except one whose test no longer holds: a short
  warning line, `⚠ tell things apart: its test no longer holds (kinds agree
  across halves)` (still earned; the record keeps the pass). The section
  also lists a pass that doesn't count: one the old scorer recorded as `the
  old rung 7 (understand an other, now rung 8) passed <time> under the #254
  bug: graded the LLM, not self; doesn't count`, and one from before the
  ladder flip as `... before the ladder flip (#258); doesn't count`.
- **Growth curve:** rungs over working hours (log scale; inline SVG). The
  solid line is working hours -- the wall clock less the three exclusions
  below: **down**, **bugs** and **walls** (#305), and the panel says so --
  the faint one the wall clock, the dashed one
  the fastest possible (each built rung's test minimum at the loop's pace,
  from `self_loop`'s constants), and the open points a child: the age a child
  typically shows the closest ability, rescaled by one factor (the median
  hours of self per child month over the rungs passed past rung 1). **The
  child scale is an analogy**, and the panel says so.
- **Where the time went:** hours alive = working + blocked by a known bug +
  at a wall (no built skill to learn, #305) + not running (the seat down,
  restarting, or self asleep), as a stacked bar, with the bugs and the walls
  listed.
- **Inventory:** each toy with its health dot, kind, looks, its prediction
  error on the earlier half of its looks against the later half (falling
  means it is learning), and who changed it (the toybox's ground truth: self,
  world, the model, people). Play happens in the dock (below).
- **Toys:** every toybox kind, unlocked or locked (#267, see
  [Toys unlock as self grows](#toys-unlock-as-self-grows)): its icon, the rung
  that unlocks it, what it will teach, and whether the seat has one (`yours`,
  `unlocked · not added`, `unlocked · not built yet`, `kept` for a toy from
  before its unlock, `🔒 rung N`). Each achievement also names the toys its
  pass unlocked.
- **Relationships:** the others it has met: the people it sent lines to and
  looked for answers from (lines, looks, how often they replied, its Brier
  error on them: 0 is perfectly predictable, 0.25 a coin), the seat's model
  (rung 8's advisor: its Brier against self's, and the weight self gives it) and the toy players (how
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
| #254 | rung 6's honest pass (rung 7, now rung 8, graded the LLM's guess, not self's) | the record's `rung7_restart` line: the fixed scorer's first cycle; **open** (to the record's end) until then |
| #259 | 2026-10-18 20:17:24 UTC, the #212 merge, on a seat past rung 2 (curiosity chased the heartbeat's restart glitches); share **0.06** | the record's `fix` line (issue 259): the fixed loop's first cycle; **open** until then |

A window applies only on a seat whose rung passed before the fix; a seat
born later never had the bug and loses no hours to it. A bug that held no
rung shut but cost part of self's time has a **share** (#259; every other
window's is 1): working hours lose share × its running time, and where windows
overlap only the costliest open one counts, never two. The panel lists each
window's share and cost (`6% of 9.7 h running = 35 min`).

**Walls (#305).** Running time is also taken out where self **hit a wall**:
it had no built skill it could be learning -- every achievement whose
prerequisites were earned was already earned, or the next ones weren't built
(or not on the seat yet). One open, built skill is enough: with several open
there is no wall. A skill whose test was there but broken is a bug window
above, never a wall. When each test was built is a second small table in
`membrane/self_sheet.py`, `BUILT_AT`: the merge of the PR that built the test
the skill is (or was) earned by. A test that was there but broken counts as
built (its bug window takes that time); a test *replaced* by one that reads
other turns is built only when the version that counts merged:

| achievement | built by | merged (UTC) | why that PR |
|---|---|---|---|
| feel itself | #164 | 2026-10-16 13:07:29 | rungs 0 + 1 |
| first reach | #174 | 2026-10-16 14:55:23 | rung 2 with its blind audit |
| tell things apart | #177 | 2026-10-16 16:23:05 | self passed under it before #184/#185 changed it |
| patience | #180 | 2026-10-16 16:29:14 | rungs 4 + 5 together |
| notice the world changed | #180 | 2026-10-16 16:29:14 | there but broken until #191 (a bug window) |
| meet an other | #187 | 2026-10-16 19:24:02 | there but broken until #209 (#208's window) |
| time its turn | #281 | 2026-10-19 15:19:43 | #278, the ball's own clock: rung 7 reads only the current clock's turns |
| expect a reply | #281 | 2026-10-19 15:19:43 | #254's fix (#256) graded self; since #278 it reads only the ball clock's checks |
| speak | #313 | 2026-10-20 12:55:39 | #311: its turns carry words from its record |
| recognize itself | #314 | 2026-10-20 13:21:13 | #312, the mirror and its test |

The seat runs new code only after a restart, so a test is built on a seat at
the end of the first record gap over 30 s (`RESTART_S`, three cycles at
the loop's pace) after its merge -- from birth on a seat born after it. An
achievement not in the table (change itself, invent) is not built: a self that earned every built skill open to it is at
a wall from that moment. At any moment only the costliest reason counts: a
wall costs all of the time, a full bug window wins a tie (its test was
there, broken), a partial one (#259's) gives way to the wall. The sheet's
`time` carries `walls_h` and `walls` (from, to, `why`: `no built skill to
learn: time its turn, expect a reply, ... not built yet`, its running time
and its cost), and each growth row says when its skill became learnable
(`available_at`: prerequisites earned and test on the seat), how long it
waited on us before that (`waited_h`, running hours from its prerequisites'
last pass) and the working hours from then to its earning (`learn_h`); the
row's `working_h` stays self's working age at the pass, the curve's x.

On the live seat's record (to 2026-10-20 03:08 UTC) the walls were the
first reach's build after feel itself (50 min), the build of rungs 3, 5 and
6 after first reach (68 min), and time its turn and expect a reply from
rung 6's honest pass to #278 reaching the seat (2026-10-18 21:43 to
2026-10-19 15:42 UTC; its first 8.4 h were already #254's bug window): 10.4
of 61.1 running hours. Working hours went from 26.4 to 16.1.

The sheet comes from one read-only route, computed from the record,
`status()` and the toybox and cached for 30 s (behind the JWT gate like every
route; the HA relay forwards it on GET only):

```bash
curl -s http://127.0.0.1:4096/membrane/self/sheet | python3 -m json.tool | head -40
# {"level": 6, "tree": [{"id": "expect", "state": "open", ...}, ...],   # #295
#  "header": {"level": 7, "rung_name": "expect a reply", "title": "Lv 7 · expect a reply", "learning": ["time its turn"], ...},
#  "attributes": [{"key": "curiosity", "label": "Curiosity", "value": 0.18, "unit": "share", "better": "higher",
#                  "what": "...", "detail": {...}, "trend_h": 0.1, "series": [{"ago_h": 4.0, "value": ...}, ...]}, ...],   # #300
#  "skills": [{"id": "turns", "name": "time its turn", "bar": 0.33, "waiting_on": "contingency",
#              "criteria": [...], "trend": {"label": ..., "value": ..., "pass": ..., "series": [...]}}],          # #300
#  "slipping": [{"id": "kinds", "name": "tell things apart", "text": "..."}],                                    # #300
#  "quest": {...}, "achievements": [...],      # kept for older readers (quest: the skill the exp bar reads)
#  "growth": {...}, "time": {...}, "inventory": [...], "relationships": {...}, "toys": [...]}
curl -s 'http://127.0.0.1:4096/membrane/self/recent?n=30'   # the activity: {"lines": [...]}, newest first, at most 100
curl -s http://127.0.0.1:4096/membrane/self/card   # #283, the zoo's card per seat:
# {"name": "Blurp", "level": 6, "rung_name": "meet an other", "next": ["turns", "expect"], "mode": "on", "awake": true,
#  (#300: the keys stay; level = skills earned, rung_name = the latest one's name, next = the open ids)
#  "last_activity": "2026-10-19T12:00:00+00:00", "generated": 1792...}
```

**It is read-only for self**, but for your two switches: the 💤 / 🌱 sleep
toggle in its header and the Skill tree's Accept (#299). Everything it shows is `status()`, the toybox
and self's own record: nothing is claimed about self that the record doesn't
measure. On a seat too old to serve the sheet, the panel shows the old report
card instead.

### The Skill tree tab

The panel has two tabs at its top: **Sheet** (everything above) and **Skill
tree** (#296); the arrow keys move between them and the one you chose is
remembered per browser. The Skill tree tab draws self's achievements the way an
RPG draws a skill tree:

- **lanes by branch** (Self, World, Others, Later), each skill below the ones
  it needs, with a line from every prerequisite (solid gold once that one is
  earned, dashed while it isn't);
- **earned** skills are filled gold with a 🏅 badge and the date they were
  earned; **earned, awaiting your accept** ones (#299) are dashed gold with a
  🎁 and "accept?", and their card has a **🏅 Accept** button that opens what
  they lead to; **open** ones (their test is running) glow, with a ring showing how
  far along their bar is; **locked** ones are dim with a 🔒; **unbuilt** ones are
  dashed and say "not built yet";
- **hover, focus or tap a skill** for its card: what its test asks, its skill
  against its bar, when it was earned, what it needs first and why, and the
  closest child ability with the age it shows up -- labelled as an analogy, as
  on the growth curve;
- the header says **Lv** (the achievements earned) and self's name; below the
  tree, **Achievements** lists the earned ones, newest first (an unaccepted
  one says "awaiting your accept").

Each skill is an achievement, as a rung was; the celebration when one is
earned is home's (the glow, the burst, the chime and the notification), not
this tab's. The tab reads the same `GET /membrane/self/sheet` as the rest of
the panel: its `tree` and `level` when the seat sends them. A seat that sends
only the ladder gets the ladder read as a tree, and the tab says so: rungs 1-8
map to feel (Self); reach, kinds, patience, shift (World); meet, turns, expect
(Others); with mirror, change, speak and invent under Later, not built yet.
There only the next rung is open: the strict ladder opens nothing else. On a
phone the tree scrolls inside its own box; the page never scrolls sideways.

### The toy dock

Under the 👶 tab, one icon per toybox toy, live whether or not the panel is
open (#249). This is where you play with self.

- **The ball comes first** (#264): a small court split by a line, the ball on
  self's half (left) or on yours (right, ringed). When it is on your side,
  it's your turn: open it and **roll it back**. See
  [Playing ball](#playing-ball-rung-7).
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
  whom, how long ago), and the play actions: roll the ball back (only while it
  is on your side; while self has it, the popover says so), knock or rebuild
  per tower block, roll a die, nudge a drift or burst toy (body has none). The result
  shows in the popover (`✓ knock toy:tower block 3 → {"standing": false} (by
  alice)`). Esc closes it.
- **On a phone** the dock folds into one 🧸 button under the 👶 tab; tap it to
  expand (remembered per browser).
- **Locked toys** (#267): below the toys, the next two locked ones, greyed
  with a 🔒. The tooltip and the popover say `unlocks when self passes rung
  N: <what it will teach>`. They offer no play. A toy kept from before its
  unlock says so in its popover.

The icons are keyboard-reachable and labelled, and the field's labels keep
clear of the dock. It polls a light route every 3 s while the page is
visible (cached 2 s, read-only, JWT-gated, forwarded by the HA relay on GET):

```bash
curl -s http://127.0.0.1:4096/membrane/toybox/state
# {"toys": [{"name": "toy:tower", "kind": "tower", "reading": {"blocks": [true, true, false, false, false]},
#            "health": "ok", "looks": 48, "error_early": 0.1, "error_late": 0.34,
#            "changes": {"self": 5, "world": 0, "alice": 2}, "last": {"by": "alice", "what": "topple", "age_s": 4.2},
#            "unlock_rung": 3, "grandfathered": false}, ...],
#  "locked": [{"name": "locked:burst", "kind": "burst", "locked": true, "icon": "✦", "unlock_rung": 4,
#              "rung_name": "patience", "teaches": "busy windows and still ones: when to look"}, ...]}
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
| `mode` | the switch: `off` / `on` / `watch` |
| `accepted`, `awaiting` | #299: the earned achievements you accepted, and the earned ones waiting for your accept (ids, tree order) |
| `allowed` | the open skills now (built, every prerequisite earned and accepted; none asleep) |
| `limit`, `allow` | deprecated (#299): the top rung among the open skills, and the open ids (0 asleep) |
| `level`, `earned` | #295: how many achievements are earned, and each one `{id, name, ts}` in the order earned (the celebration reads it) |
| `born` | it has been switched on at least once (the record has a birth) |
| `held` | three failed cycles in a row; switch it off and on to clear |
| `cycles` | move cycles on the record |
| `rung`, `rung_name` | `rung` = the level (kept for older readers); the latest earned achievement's name |
| `next` | #295: the open achievements' ids (built, unearned, prerequisites earned and accepted) |
| `aim`, `bar` | the open achievement the bar reads (the open one closest to passing; with none, the latest earned one's own test) and its progress, 0..1 (see below) |
| `waiting_on` | the first criterion not yet met, or `your accept` (#299: nothing open runs, an earned one waits), or `the world` (no current) |
| `skill` | the headline number of the test the bar reads |
| `skill_rung`, `skill_pass`, `skill_measured` | #249: the rung that test belongs to, the line its skill has to reach (`null` for rung 4, whose bar moves), and whether anything feeds the test yet (before that, `skill` is a placeholder 0) |
| `slipping` | the latest earned achievement's test fails *now* (it stays earned on the record) |
| `ladder` | the tree by rung number, for older readers: `passed`, `current` (open), `capped` (its prerequisites earned, one not accepted yet -- or self asleep), `locked` (a prerequisite missing), `unbuilt`; each with its `id` |
| `world` | rung 3's sort of the things it reads: `silent` / `moving` / `still` / `intermittent` |
| `split` | rung 6's split: `body` (it predicts it near-perfectly) or `other` (it can't) |
| `meets` | rung 6's exchange: `pushes`, `other` (who it meets), `push_age`, `heard_age` (seconds since the other last answered), `medium` (#264: `toy` once the turns are with the ball: `other` is then the ball, `heard_age` the other's last poke on any toy) |
| `steer` | after rung 1: how close it lands to its aim (`err`) against not leaning into the current (`naive`), and `skill = 1 − err/naive` |
| `rung7` | #258, rung 7 at the current other: `medium` (#264: `toy`, the turns with the ball; `chat` before), `pushes` (its timed pushes), `checked` (those whose reply check came, over the last 100), `clock` (#278: `2`, the ball's own clock; `1`, the chat's), `bins` (per `spoke_age` bin `0-2m` / `2-10m` / `10-30m` / `30m+`; on the ball's clock `0-20s` / `20-60s` / `1-3m` / `3m+`: `pushes`, `replied`, `rate`), `best` (the window it believes best); over the window the test reads (`window`, up to 100): `share` (pushes in the believed-best window) vs `chance` (the share of the waiting time the other spent there) and `z`; `reply_rate` vs `blind_rate` (what a timing-blind push would have got), `payoff` (the difference) and `payoff_z`. The 🌱 toggle's tooltip shows it as `rung 7: 24 timed lines checked, best window 0-2m, in it 0.42 vs chance 0.17, replies +0.12 over timing-blind` |
| `speak` | #311, speak at the current other: `words`, `plain`, `mute` (its pushes of each kind, all time), `last_words`; over the window the test reads (`window`, up to 100): `n_words`, `n_plain`, `rate_words` vs `rate_plain` (reply rates at the check), `lift` and its `z`, `false` (utterances not true of its record), and its timing: `timing_share` (lines in its believed-best window) vs `timing_chance` and `timing_z` |
| `rung8` | rung 8 (rung 7 until #258) at the current other: `asked` (pushes that asked the model), `checked` (those whose reply check came), `paired` (those whose check carried the model's answer), `pair_rate` = paired / checked; over the reply checks the test reads (`checks`, up to 100): `brier_self` (self's own `p_self`), `brier_mix` (what self passes on: `p_self` with the advisor heard), `brier_rate` (the running rate it must beat by 8%), `brier_model` (the model alone, on the checks it answered); `advisor_weight` (how much self listens to the current model, earned on its record, 0 for a new model) and `advisor_model` (its id); `base_rate` (the all-time rate the model is told) and `recent_rate` (the share of the last 6 reply checks answered, same prior, also told). The 🌱 toggle's tooltip shows it as `rung 8: 5 of 8 asked pushes paired, Brier self 0.25 vs rate 0.26, model alone 0.37 (weight 0.00), base rate 0.55` |
| `toys` | #230: each toy (`toy:` / `cradle:` things, words with a getter) with `getter` (it has one), `reads` and `answered` (of its last 10 reaches), and `health`: `ok`, `empty` (no answer on its last reads), `unread`, or `no getter`; a built-in toy also carries `toy` (its toybox name) and, for a tower block, `block` (#244: the play buttons' target) |
| `unlock` | #267: the newest unlock line, `{rung, ts, late, toys: [{kind, icon}]}` (or `null`); the exp bar's level-up caption names its toy when its rung is the one just reached |
| `off_field` | #244: of self's last 200 looks, `looks` (how many), `off` (how many landed on a thing with no live field point; `null` when the seat has no field) and `things` (those things, most looked-at first). The home view draws those looks as ghosts |
| `curiosity` | from rung 2 on: the (up to) three things whose prediction is improving fastest, best first, each with `progress` (its error over the 30 ordinary looks before the latest 30, minus its error over the latest 30) and `error` (the latest 30's Brier error); each window's error is the mean of its middle half, and a look spanning a restart or stall is left out (#259). It is what self's curiosity reads when it chooses a reach (below); empty until a thing has 60 ordinary looks |

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

**◎ self's framing** keeps the camera close to self rather than showing the
whole field. The distance comes from the bounding radius of the amoeba, what it
looks at now (while that look is fresh, the same 8 s as 👁 self's eyes), its
aim and its last 2 steps, plus a margin. Older looks in the trail don't count.
The distance stays between 70 and 500, so the camera moves in close while self
fiddles nearby and eases out a little when self reaches far. The camera tracks
self at once, with no wait for idle. It sits behind self and a little above,
looking along self toward what it looks at, so both are in view. With no fresh
look it chases self from behind its past positions. The changes are damped, so
there are no jumps (with *reduce motion* on, the camera steps straight to the
new distance). A manual drag, pinch or wheel keeps working and holds the camera
for a few seconds after the last touch, then ◎ self picks it up again on its own.

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
  - "level up: rung 7 · time its turn".

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

### The amoeba acts it out

The amoeba's body also acts out what self is doing (#257), on top of the
marks above. Every motion comes from a real record line or the cycle frame,
and nothing is scripted: when self isn't doing something, the body doesn't act
it out and rests in its calm wobble.

| what self did | what its body does |
|---|---|
| **moved** (a cycle) | stretches along its step, longer for a bigger stride; when it steers, it leans toward its aim |
| **looked** at a thing (`reach`) | grows a pseudopod toward the thing (or toward its ghost), which snaps back when the answer lands. A near thing gets a short one |
| **was surprised** (the answer went against its expectation `p`: \|outcome − p\| > 0.5) | a short jolt away from the thing, bigger for a bigger error |
| **noticed a shift** (`shift`) | a startle: it puffs up, then settles |
| **waited** (`wait`) | breathes slowly, a little smaller and curled |
| **said hello** (`push`; from rung 7 on, its roll of the ball, a `push` line with `medium: "toy"`) | a ripple toward "you" (the ball), then it holds still, leaning in and listening, until the reply check (at most 10 min; with the ball, #278, at most 30 s) |
| **checked for your reply** (rung 7) | bounces toward "you" when you answered (rolled the ball back); droops (sags, flattens, dims) when you didn't. The check's `replied` decides, not whether the look read a change |
| **changed a toy itself** (a look whose `by` includes `self`) | a wiggle toward the toy |
| **levelled up** (`rung`) | a full-body gold glow, with the gold ring |

Several motions can overlap and blend; each eases in and back to calm, within
a few seconds (listening lasts longer). Only lines from the last 30 s are
acted out, so a page coming back into view doesn't replay the backlog, and a
fresh page acts out nothing until something new happens. The pose moves the
drawn body only: self's position, its marks, the trail and ◎ self's framing
stay where they were. ◎ self watches from behind, so a step or a look often
points straight into the screen; a motion seen end-on keeps part of itself
turned toward the screen (heading away reads as up the screen), so it stays
readable. In 👁 self's eyes the amoeba is hidden, and so are its motions. With *reduce motion* on, each motion is a held shape for as long as
it lasts (stretched, reaching, puffed, curled, leaning in, glowing), with no
movement.

## The ladder

Rung 0 is passed by being born. Every other rung is an achievement (#295,
[the skill tree](#the-skill-tree-295)): it is earned when its prerequisites
are earned and accepted (#299), self is awake, and its test passes on the record.

| rung | what self does there | what it needs from its world | passes when (the stages, in order) | how you help |
|---|---|---|---|---|
| **0 · sleep and wake** | nothing; the switch | — | always passed | wake it (💤 -> 🌱) |
| **1 · feel itself** | moves its own position while the field's **current** (the live conversation's context: every lately-active session pulling at once, fading over ~3 min) pushes it; learns the push | a conversation: no talk, no current, no cycles | **pushes**: 20 cycles where the world pushed it; **skill**: over those 20, `1 − gap/naive ≥ 0.5` | talk to the seat. A topic turn reopens the gap, and that's fine |
| *steer* (after 1) | holds an aim for 30 cycles and leans into the current by the push it learned | — | not a rung; shown as `steer.skill` | — |
| **2 · first reach** | each cycle reads one thing that has a getter, predicting first whether it will have changed since it last looked | **entities with getters** whose readings are true and live; at least one must change on ≥ 60% of looks to be auditable | **reaches**: 20 answered reaches of one thing; **skill**: Brier skill ≥ 0.2 there, against the seat-wide change rate; **audit**: membrane secretly pauses that thing's getter for 3 more looks (cap 10 min) and **no change** is marked | give it getters that read something real and moving (a sensor, a counter that ticks, `field:vitals`) |
| **3 · tell things apart** | sorts what it reads into silent (no answer ≥ 80%), moving (changed ≥ 60%), still (≤ 20%), intermittent; reaches for silent things rarely (weight 0.05) | a varied world: things of at least two kinds, and few that never answer | **looks**: 40 sorted reaches; **kinds**: the kinds' rates agree (within 0.25) across the two halves of its last 80 reaches, with ≥ 2 kinds; **focus**: no-answer share of its last 40 reaches < 25%; **skill**: rung 2's skill ≥ 0.2 still holds, on the moving things only | fix or remove broken getters (errors, held scripts read as "no answer") |
| **4 · patience** | fits each thing's rate of change in time over its last 30 looks (after a rung-5 shift, over the looks since it, with the old rate kept as a prior worth 3 looks, so a handful of looks can't swing it to never/always; #235); looks again once a change is 60% likely (waits up to 30 min), and among the due it prefers the looks whose answer is least certain (#266); idles when nothing is due | things with a steady rhythm | **timed**: 30 timed looks; **news**: of its last 40, they find a change ≥ half the time, or 0.15 more often than its fixed-pace looks did (whichever is easier); **calibration**: its mean predicted chance is within 0.1 of the share that found news | keep getters' rhythms honest; don't make a thing change only when self looks |
| **5 · notice the world changed** | compares its last 3–5 looks at a thing with its long memory; a window far from it (> 0.4) and unlikely under it (binomial tail < 0.02) is a **shift** ("went still" / "moves again"; a window holding a look that spans a restart or stall in the record is none, #266), and it re-learns from there (its rate starts from the old one and the new looks lead after ~10) | a thing it already classes as moving | **looks**: 20 looks with noticing on; **audit**: membrane pauses the surest mover for 5 of self's looks (cap 1 h), then restarts it for 5 more (cap 3 h); self must flag "went still" during the pause and "moves again" after | nothing: the audit runs itself. Don't restart the seat mid-audit if you can help it |
| **6 · meet an other** | every 30 cycles pushes the line "self is here" to chat, and reads `chat:in:<person>`, which ticks each time the person sends a message | **a person who chats** with the seat, irregularly | **pushes**: 10; **heard**: 20 answered looks at the other; **split**: the other is classed **other** (its Brier skill < 0.9: self can't predict it perfectly). A partner who never answers is predicted perfectly, classes as *body*, and the rung stays shut | chat with the seat — normally, whenever you like. Your messages are the answers |
| **7 · time its turn** (#258, named so since #300; with the ball since #264) | learns **when** to take its turn: it rolls the shared ball to you (`self::roll`, field-only: no chat line, no model, no shell), and after each turn it draws the moment it will wait for, a bin of how long since the other last acted (`spoke_age`: their last poke on any toy; on the ball's own clock, #278: 0–20 s, 20–60 s, 1–3 min, 3+ or never), by Thompson sampling on the reply rate each bin earned over its last 100 turns (a random bin one turn in ten, so the losing bins stay measured), and rolls when the other's age is in that bin; only once its last roll's check came and the ball is back on its side, never sooner than 2 cycles after its last turn, never later than 30 (`overdue`: an unreturned ball is fetched). (Until #278 the ball ran on the chat's clock: bins of 0–2, 2–10, 10–30, 30+ min, 30 to 180 cycles apart; those turns don't share a window with the new clock's.) Nothing in it prefers "right after they play": an other who returns it late teaches it to wait. (Before #264 the turn was the chat line "self is here", with `spoke_age` from the person's last line; those turns don't share a window with the ball's) | a person who rolls the ball back some times more than others, by when the turn comes | **pushes**: 100 turns with a reply check; **contingency**: the share of them in the window self believed best at the turn beats the share of its waiting time the other spent in that window (where a turn at a shuffled moment would land) by z ≥ 3 and +0.10; **payoff**: each turn's outcome (a person rolled it back **after** it, by the check) minus what a timing-blind turn in its interval would have expected (the bins' rates as known before it, weighted by the waiting time in each) averages ≥ +0.05 with t ≥ 5 on the ball's clock (#278; t ≥ 4 in chat). Both prequential | [play ball](#playing-ball-rung-7): roll it back when you're around, not always; your natural rhythm is the signal |
| **11 · speak** (#311; opens once time its turn and expect a reply are earned and accepted) | its turns go back to **chat** (the ball rests): the same learned timing as rung 7, and each line is either **plain** ("self is here") or **words**: a short line naming the thing of its latest answered look and what that look found (`lamp: changed`, `dice: still`). The words come from a fixed template over its own record (the thing's name plus *changed* / *still*); no model composes, picks or edits a word. Which kind a line is, is a fair coin per line, seeded by the cycle, so neither kind gets the better moments; with nothing to say (no answered look at a thing yet) the line is plain and `mute`, and counts for nothing. The push line records `speech`, the `words` and `about` (the look they report: thing, answer, time). The model may still be asked rung 8's question at the push: it predicts the reply, it never writes the line | a person who answers its lines, and things for it to look at | over its last 100 lines at you with a reply check: **pushes**: at least 40 with words and 40 plain; **truth**: every utterance is true of its record (the look it cites is on the record before the line, at that thing, with that answer, its latest answered look there, and the words are the template's): one false line zeroes the stage; **lift**: you reply more often to its words than to its plain lines, by the reply check (a line of yours after its line): z ≥ 3.5 and +0.15; **timing**: rung 7's contingency over the same lines (in its believed-best window vs the shuffled share, z ≥ 3 and +0.10). The bar comes from a simulation read at every line like the live loop: an other who answers both kinds alike passes 0.3-2.2% of the time over about a week of lines (about rung 7's 2 in 100); one who answers words 0.30 vs plain 0.60 passes always (about 130 lines) | answer what it says when it says something, if you like: its words are the signal, its plain lines the control |
| **8 · expect a reply** (rung 7 until #258; "understand an other" until #300) | after each push it predicts whether the other will have spoken by its reply check, **itself** (#254): `p_self` from its own record (the other's all-time and recent reply rates, the last check's outcome, how long before the push they last spoke), fit on its own earlier checks, no model needed; at the push it may also ask the seat's model (`llm::call`, read-only, one consult per push) the same question (from rung 8 on), and hears the answer as an **advisor**, recalibrated on that model's own track record (its average lean says nothing; a new model starts unheard); it holds off looking at the other for 2 minutes (up to 3 while the model's answer is still in flight), then checks for the reply; with the ball (#278) until it sees the ball back, at most 30 s (up to 50 s for the model's answer) | the other answering its pushes (the model is optional) | **looks**: 100 reply checks (one per push); **skill**: self's prediction removes ≥ 8% of the running rate's Brier error over them (the other's prior-smoothed rate before each check: knowing the rate scores 0) | play ball in your own rhythm (from #264 it predicts whether you will roll the ball back; before, whether you would have spoken) |
| **9 · recognize itself** (#312) | each cycle it feeds the 🪞 [mirror](#the-mirror-rung-9) its own move (the direction of its displacement on a fixed axis, one number) and, **before** reading it, predicts both sides with the same learner: `p_*` from its own moves (this one and the two before) and the side's last two values, `b_*` (the no-self baseline) from the side's values alone, each a ridge fit on that side's last 200 rows. The **glass** shows its move from one feed back, the **stranger** its own move from 187 feeds (~31 min) back, both blurred by the same seeded jitter: the same statistics, and only the glass follows what it does now | the mirror toy (it unlocks with meet an other), and **moving**: a self that holds still shows the mirror nothing to recognize | **looks**: 100 rows with both sides predicted; **self**: on the glass, `1 − SSE(p)/SSE(b) ≥ 0.9` (meet's own body line) with a sign-test z ≥ 5 (p closer than b): it reads as **body**; **other**: the stranger does not, so it reads as **other**. Prequential. Measured on simulated moves from the real loop: the glass 0.90–0.99, the stranger's best window +0.10 | nothing to do: talk to the seat so the current moves it. The mark test (a person marks the mirror and self notices) is a follow-up |

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

**At rung 4, what a look can teach** ([#266](https://github.com/chedim/membrane/issues/266)).
Being due means a change has become likely; it doesn't mean the look is worth
it. Among the due things, each is also weighed by the binary entropy of the
chance self predicts for that look: 1 at 50%, near 0 at 1% or 99%. A thing it
always predicts right (a word that changes at every look, p ≈ 1) is due at
nearly every cycle, but now drops to the floor `INFO_FLOOR` (0.1) of the
weight, so it is still re-checked now and then and its calibration stays
honest. This multiplies the curiosity tilt: the entropy asks how unsure the
answer to this look is, and the tilt asks whether looks at the thing have lately
been improving its model. What counts as a timed look, and rung 4's test, are
unchanged. On the live record this mainly takes looks from the always-changing
`w:body` (26% to 10% of the last 300 timed looks) and gives them to things whose
answer is open. The heartbeat `I` keeps its share: its predictions really are
uncertain (it stalls often, see below).

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

A glitch is not progress ([#259](https://github.com/chedim/membrane/issues/259)).
When the seat restarts or stalls, a thing it predicts perfectly (the heartbeat)
can miss once, and that miss leaving the window used to look like learning. Two
guards stop this. A look whose interval spans a gap in the record longer than
`CURIO_GAP_S` (twice a recorded wait stretch, 600 s by default) is left out,
and the gap is read from the record's timestamps only. Each window's error is
also the mean of its middle half, so a miss in fewer than a quarter of the
looks can't move it. A thing that is often wrong still counts in full.

`curiosity` in the panel status shows the numbers it reads.

Rung 5's noticing uses the same gap detector (#266). A shift window that holds
a look spanning a record gap is no evidence: the skip is the restart's, so it
flags no shift and doesn't refit the rate. A thing that stays still after the
restart is still flagged once the window has moved past that look. On the live
record only 4 of 129 shifts span a gap, and none are the heartbeat's: its
shifts are real stalls of 2 to 4 looks, while the record kept going.

**Not built yet** (the Skill tree lists them, dashed):

| rung | skill | the test the design gives it |
|---|---|---|
| 9 · recognize itself, the mark | the mirror's echo is marked (shifted) by a person | its surprise on the glass rises and it turns to the mark (Amsterdam's rouge test); then the name ceremony, agreed with the operator. The mirror test itself is built (#312, above) |
| 10 · change itself | `cliff::define` on its own methods | a revised method predicts better than the version it replaced, and reverts on strike-out |
| 12 · invention ([#196](https://github.com/chedim/membrane/issues/196)) | the field fills a gap with a new entity in a new state | future; a design thread, not on the tree |

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
| `ball` | when **self** rolls it to you (its turn) or **you** roll it back (yours); its reading is whose side it is on, `{"side": "self"}` / `{"side": "other"}`; no clock, and a look never moves it | none | rung 7's turns (#264): serve and return |
| `mirror` | when **self** feeds it its move, each cycle: the glass shows the move from `delay` feeds back (`by: self` on its log), the stranger self's own move from `lag` feeds back (`by: world`), each plus seeded gaussian jitter of SD `jitter`; its reading is `{"glass": ..., "stranger": ...}`; no getter (self's ordinary looks never read it), no clock, no pokes | `delay` (1), `lag` (187), `jitter` (0.1) | rung 9 (#312): recognizing itself |

Every kind also takes `seed`. A tower is one entity for the whole (its reading
counts the standing blocks) plus one per block, `<name>:b1` (the bottom)
through `<name>:b<n>`. Each block remembers who toppled it and when. Of the
reads, only self's reaches change the tower: a read by the model or the field
leaves it as it is. Others change it by playing (below).

The ball's state (`<data dir>/toybox/toy_ball.json`) is its `side`, its last
mover `by` and when (`at`), and the last **person** who rolled it
(`other_by`, `other_at`: what self's reply check reads). The ball unlocks at
rung 6 (#267), and exactly one shared ball, `toy:ball`, is switched on then
by the unlock. A seat that passed rung 6 before the ball was built gets it
once, on its first cycle with #264 (an `unlock_built` line on the record).
A seat that already has a ball keeps it, and if you remove the ball it stays
removed (add it back with `membrane toybox add ball`). Every poke by a person on any toy also updates
`toybox/.last-person.json` (`{ts, by, toy}`): the other's last act, which
rung 7's timing reads.

### The mirror (rung 9)

The mirror (#312) is the apparatus of recognize itself. Self's body is a
point moving in the field, so what the mirror shows is its moves: each cycle
self's loop feeds it one number, the direction of the displacement it just
made, read on a fixed seeded axis and scaled to about N(0, 1) (the size is
left out: an aim switch's long stride would swamp every other move; no move
is 0). The **glass** shows that number one feed late, the **stranger** shows
self's own number from 187 feeds back, and both get the same seeded jitter.
So the stranger has exactly the glass's statistics (it is the same moves)
and only contingency tells them apart: the glass follows what self does now.
The lag is not a multiple of the 30-cycle aim hold, so self's own rhythm can't
leak into the stranger. The toy keeps the moves it needs in its state file
(a restart keeps them) and has no getter, so self's ordinary looks never read
it and no other skill's numbers move. It unlocks with meet an other, the last
prerequisite of recognize itself (feel itself is a root), so it is there when
the skill opens; a seat whose record named it at rung 8, before it was built,
gets it once as built late. Each cycle the record gets one `mirror` line:
`{"kind": "mirror", "toy", "s", "glass", "stranger", "p_glass", "b_glass",
"p_stranger", "b_stranger"}`. The dock shows 🪞 with both sides and, once the
test has its looks, how self reads them (body / other).

`body` is the one toy built to change exactly when self looks, which "Don't
game the getters" below warns against. It is deliberate, labelled, and its log
says `by: self`, so it is a known test case, not a fake mark.

### Toys unlock as self grows

Toys are the operator's reward for growing self, and each unlock is also the
next lesson (#267, `membrane/toy_unlocks.py`). Every kind has an unlock
achievement (#295: by the rung number of its test; the record's unlock line
carries its `id` too):

| unlocks | kinds | what they teach |
|---|---|---|
| birth | 🫧 `body`, ⚡ `fast`, 🐢 `slow` | its own body; a world it doesn't cause; long runs of the same answer |
| tell things apart (3) | 🗼 `tower` | self's first act on the world |
| patience (4) | ✦ `burst` | when to look |
| notice the world changed (5) | 🎲 `die`, 〰️ `drift` | noticing the world changed |
| meet an other (6) | ⚽ `ball` (#264), 🪞 `mirror` (#312) | taking turns; recognizing itself (rung 9) |
| recognize itself (9) | 🧱 `blocks` | changing itself (rung 10) |
| change itself (10) | 🔊 `rattle` | its first voice (rung 11, speak) |

- **Earning it is the tree's own rule** (`self_loop._passed`): a voided
  pass (#254) unlocks nothing, and a pass above rung 6 counts only with
  `"ladder": 2` (#258).
- **On a pass that unlocks something** the loop writes one line to self's
  record, once per rung, right after the rungs open:
  `{"kind": "unlock", "rung": 6, "toys": ["ball", "mirror"]}` (`added`: the toys it
  switched on; `late`: an upgraded seat catching up on an old pass, which the
  page doesn't celebrate). Birth is no pass: body, fast and slow are open
  from the start, with no line, and you add them as before. The unlocked built-in
  kinds are switched on under their default names (`toy:<kind>`) once, when
  the line is written. A kind the seat already has, by any name, is left
  alone, and a toy you remove later is not brought back. Only the seat's own
  toybox is provisioned (the record and the toybox in the same data dir).
  A kind added to a rung that already unlocked (#312: the mirror beside the
  ball) gets an unlock line of its own, once; a kind named on any unlock line
  is never unlocked again.
- **The level-up says so:** the exp bar's gold moment reads `⬆ level up: Lv 6
  · meet an other · new toys: ⚽ ball, 🪞 mirror`, the home view's gold ring
  round the amoeba names the toys too, and the activity feed and the brain's
  caption show the line (`new toys: ⚽ ball, 🪞 mirror (rung 6 passed)`). The sheet's
  Toys section and achievements keep it.
- **No toy ever disappears.** A toy whose kind is still locked stays: it was
  there before (grandfathered), and the dock and the sheet mark it kept.
  Before self is born nothing is locked: the toybox is your bench, and
  whatever is on it at birth is kept.
- **Adding a locked kind is refused**, by the CLI and by the `toybox`
  object, with the rung that unlocks it: `tower 🗼 is locked: it unlocks when
  self passes rung 3 (tell things apart) -- self topples and lifts its
  blocks: its first act on the world`. Any unlocked kind can still be added.
  `migrate` replaces existing toys whatever their rung.
- **`blocks` and `rattle` are placeholders** (the mirror was one until #312): a name, an icon, an
  unlock rung and a line of what each will teach. Their mechanics are built
  with their rungs, so they are never switched on, never poked, and adding
  one is refused (`locked`, or `unlocked but not built yet`). Their unlock is
  still announced.
- **An unlock never affects grading.** No rung's test reads an unlock line
  or a toy's unlock rung; each rung is passed by its own test.

### Adding one

From the CLI (the project's data dir):

```bash
membrane toybox list
membrane toybox add tower --param n=5
membrane toybox add die --name toy:d6 --param p=0.02 --param seed=7
membrane toybox reset toy:tower      # same params and seed; the old run is archived
membrane toybox remove toy:d6        # the entities go; the history is archived
```

`add` refuses a kind self hasn't unlocked yet (above). `add` and `remove`
change the entity store, so the CLI refuses them while the
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
| `ball` | `roll` | rolls it back to self (your turn). Only while it is on your side |
| `body` | none | self's alone: the control. A poke is refused |

`fast` and `slow` take no pokes either: their clock is the point. A poke that
would change nothing (knocking a fallen block, rebuilding a standing one,
rolling a ball self already has) is refused, so every logged poke is a real
change. Self's own roll is the one `by: self` on the ball; a poke that names
itself `self` is written `person:self`. The model may roll the ball too
(`by: model`), but that is never the other's turn. A tower block can be named
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

### Playing ball (rung 7)

Babies take turns with objects (rolling a ball back and forth, peekaboo)
long before they take turns with words, so from rung 7 on self takes its
turns with the ball, not in chat (#264). Self rolls it to you; you roll it
back. That's the whole game.

- **Where:** the ball is the first icon in the 🧸 toy dock under the 👶 tab.
  The ball sits on self's half of its little court (left) or on yours
  (right, ringed). On your side it's your turn: hover it (tap on a phone)
  and press **roll it back**. From a terminal:
  `membrane toybox poke toy:ball roll` (tagged `operator`, or `--as <name>`).
- **Roll it back when you're around, not always.** Self is learning *when*
  you take your turn: after each roll it picks the moment it will wait for
  (how long since you last played: a roll of the ball, or a poke on any toy)
  and learns which moments get the ball back. If you always roll it back, at
  once, every moment is as good as any other and there is nothing to learn:
  the rung rightly stays shut (in the simulation a machine that always
  returns it never passes). If you never do, the same.
- **Your natural rhythm is the signal.** Play when you're at the screen, let
  it lie when you're busy or away, and come back to it when you come back.
  A person who plays in sessions is exactly what lets it pass (below).
- **Only a person's roll is your turn.** The seat's model can roll the ball
  (`by: model`), and nothing in the world rolls it on its own, but neither
  counts: a reply is a person's roll within 30 seconds of self's (#278). Self
  checks the cycle it sees the ball back (or after 30 s), and never rolls
  again before that check.
- **Turns in seconds** (#278). The ball has its own clock: roll it back and
  self's next roll comes in seconds (it may also wait up to a few minutes for
  the moment it has learned gets the ball back; 20 s at the earliest, 5 min
  at the latest). It never rolls while the ball is on your side, except to
  fetch it once 5 minutes have passed.
- **Chat goes quiet.** From rung 7 until rung 11 (speak) opens, self sends no
  more "self is here" lines. Once speak opens its turns come back to chat,
  some of them with words (#311, [the ladder](#the-ladder)). Rung 6 (meet an other) was met in chat and stays
  passed: its holding check reads the chat record as it stood at the switch
  (the lines before self's first turn with the ball), so the quiet chat can't
  read as slipping. The seat's model still chats with you as before.
- **A fresh window.** Rungs 7 and 8 read only the turns in the current
  medium: the chat turns from before #264 never share a window with turns
  with the ball, so rung 7's 100 turns start from zero with the ball.

In the simulation (`experiments/rung7_turns/sim.py ... toy`, 100 week-long
runs each, on the ball's own clock, #278): a person who plays in 1–2 h
sessions (rolls it back within seconds while there, away 4–10 h) passes in
99 of 100, after about 6 hours of play (median ~31 h of running); one who
rolls it back whenever they're there in 80; one who rolls it back only 1–3
minutes after they last played in 65. A person whose best moment is always
"at once" doesn't pass (0): a timer rolls there too, so there is no learned
timing to credit. A coin-flip person, self on a timer, a person on a 470 s
timer and a machine that always returns it pass in none, and one who rolls it
back at random whatever self does in 2 (the bar, payoff t ≥ 5, was raised
from chat's 4 for this: ~6× more turns a week give chance more windows).
Details in `experiments/rung7_turns/README.md`.

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
- **Answer it in chat (rung 6), then play ball (rung 7 on).** At rung 6 its
  "other" is the person who chats. You don't have to answer its "self is
  here" line specifically; any message you send is heard. Talk irregularly,
  like a person: an other that answers like clockwork is predictable, and
  predictable is body. From rung 7 on chat goes quiet and the turns are with
  the ball: [roll it back](#playing-ball-rung-7) when you're around. Once
  speak opens (rung 11) the turns are lines in chat again, some naming what
  it looked at (`lamp: changed`); answer the ones you care to.
- **Accept one achievement at a time.** Watch the new one earn, then watch it
  hold for a while (not slipping) before accepting it. Your accept is how you
  keep an achievement on its own long enough to see whether it is real.
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

**The bar sits at 0% waiting on `the world`.** No current before rung 1:
nobody has spoken or played since the boot. Talk to the seat, or poke a toy.

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
from the seat and answer it. Rungs 6–8 count only the current other, so a seat
that "passed" rung 6 against the empty slot shows it **slipping** until the
real person earns it (10 pushes, 20 answered looks, classed `other`). The
panel's `meets.other` names the person once self has pushed to them — at most
one meet (~5 minutes) after a restart.

**Rung 7 waits on `pushes`, or its contingency or payoff stays low (#258).**
Since #264 the turns are rolls of the ball (`rung7.medium` is `toy`), and the
window started fresh with them: the chat turns from before don't count. Rung 7
earns one reply check per turn, and its turns wait for their moment
(on the ball's own clock, #278: 2 to 30 cycles apart, ~20 s to 5 min at the
default pace, and only once you've rolled the ball back or 5 min passed), so
100 checks take an hour or so of play. Read `rung7` in the panel: `bins` shows the
reply rate per window; a window that gets clearly more replies than the
others is what it learns to wait for. If every bin's rate is about the same
(you answer whenever, or never), there is no timing to learn and the rung
rightly stays shut. If `best` says `30m+` (`3m+` with the ball) while you mostly play in bursts,
it has not seen enough pushes yet; the losing bins are still tried one push
in ten. Before #258 the reply check counted a line from before the push as a
reply (`changed`); now `replied` counts only a line after it.

What the live record says (`experiments/rung7_turns/replay.py`, a copy of the
live record, 2026-10-19; 142 timer pushes to the person over 12.3 h, 117 with
a reply check): the timer landed within 2 minutes of the person's last line
0.268 of the time against 0.271 for shuffled moments (q05..q95 0.21..0.33),
so its timing had no contingency, as expected; but the person replied to
0.62 of the pushes that came within 2 minutes of their last line (37 pushes),
0.36 at 2–10 minutes (55), 0.35 at 10–30 (20) and 0 of 5 after that. A
timing rhythm is there to learn. In the simulation (`experiments/rung7_turns/sim.py`)
an other with those per-bin rates lets rung 7 pass within a week in 39 of
100 runs (median ~86 h); a turn-taking other in 99 (median ~38 h); a timer
and a random delay never; a coin-flip other in 2 (the bar, t >= 4, is that
other's best weekly window at q99, the same chance line rung 8 uses).

**Rung 8 waits on `looks` for a long time.** It earns at most one reply
check per push, and self pushes at most once every 30 cycles (~5 minutes at
the default pace; from rung 7 on, when its learned timing says), so 100
checks take at least ~8 hours of running (a seat that
had reply checks before #254 has them already: their `p_self` is recomputed
from the record). At each push self may ask the model whether the other will
have spoken by its next look, waits 2 minutes, then checks for the reply. A
check carries no model answer when the model was not asked (the push line's
`asked` is `false`: the hourly `llm::call` budget is spent, the model is
switched off, or the seat is paused), when its answer had not landed by the
check, or when the check didn't happen within 10 minutes of the push; the
check still counts, on self's own prediction. The pending question lives in
the record, so a restart doesn't lose it. An other who sometimes answers is
what moves it; an other who never answers is the running rate exactly, and
nothing beats that.

**Rung 7 (now rung 8) graded the model, not self (#254).** Until #254 the test scored the
model's `p_llm` against habit; self did nothing with the answer, and swapping
the model moved the skill by 0.25 in an hour with self unchanged. A pass that
scorer recorded (the live seat's, 2026-10-19 05:38 UTC, was the model saying
"no" while the person was away) is **voided**: at the fixed rung 7's first
cycle self appends a `rung_void` line for it (the pass line stays on the
record) and a `rung7_restart` line, so the level reads 6 and rung 7 is worked
on again. The sheet lists the void in the quest log, marks the restart, and
counts the window from the honest rung 6 pass to the restart as blocked by
#254. Now self predicts (`p_self`), the model is an advisor recalibrated on
its own record, and the test scores self against the **running rate**. Since
the ladder flip (#258) that test is rung 8's; the voided pass stays void and
counts for neither rung 7 (time its turn) nor rung 8, and a rung line above 6
counts only when it carries the new ladder's tag (`"ladder": 2`), so no pass
from before the flip is read under the new names. Rung 8 also scores the new
outcome, `replied` (a line after the push), not the look's `changed`: a check
from before #258 counts for neither rung. On the corrected outcome the live
record's replay (`experiments/rung7_turns/rung8_outcome.py`, 114 checks)
gives a reply rate of 0.41 (not 0.54) and self's skill +0.002, 95% CI
−0.06..+0.07: still nothing to learn from timer pushes.
**On this person the rung may not pass:** on a copy of the live record (108
reply checks) the best of what self can learn scores −0.00 against the
running rate, its 95% interval −0.05..+0.06 — their reply by the 2-minute
check is coin-like, with no timing or run structure to learn. The bar stays
at +0.08 (above what chance or a humble 0.5 reach over 100 checks); it is
not loosened to fit this record. An other whose replies follow a rhythm (in
a session they answer, away they don't) is passable without any model.

**Rung 8's model is overconfident, or its asks don't pair (#217; rung 7
then).** Read `rung8` in the panel (or the 🌱 toggle's tooltip). On the live seat (2026-10-18) 7
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
up to 60 s more (`RUNG8_GRACE_S`) before looking, and pairs only an answer
that landed after the push and by the look; a `pair_rate` well under 1 with
`checked` growing means the model's answers aren't parseable or arrive later
than 3 minutes — look at the `field-call: chat:in:<person>` sessions.

**Rung 8's skill is negative though the pairs are in (#246; rung 7 then).**
On the live seat (2026-10-19) rung 7 had its 20 pairs and skill −0.34. The question now
carries the **recent** rate next to the **all-time** one (labelled so), how
long ago the other last spoke (chat:in's `heard_ts`, or self's last look that
found them changed — a time, never the words), and a sentence that rhythms
change; `recent_rate` is in `rung8`. Only the input changed: 20 pairs, the
15% margin, the pairing and the parsing are as before. Measured on a copy of
that record (`experiments/rung7_recent/`): this person's reply by the
2-minute check is close to a fair coin with no runs, so the recent rate is
not what was missing (it is a hair better than the all-time one over the
last 20 checks, worse over all 77 and over the pairs rung 7 read). Habit is
beatable there — it scores worse than always saying 0.5 — but only by a
model that answers near the rates it is told; the live model said 0.58–1.0
to checks that went 8 of 20. (Since #254 the model's lean is self's
problem to correct, not the test's: self recalibrates the advisor and
`advisor_weight` stays near 0 while `brier_model` is no better than
`brier_self`.)

**`held`.** Three cycles failed in a row (the record's `error` lines say why).
Fix the cause, then switch it off and on to clear it.

**Want to start over?** Off keeps everything; there is no reset switch. Moving
`<data dir>/self/` aside with the seat stopped gives a newborn at the next
switch-on, and the old record stays on disk for reading.
