# rung 7, take turns: the replay and the simulation (#258, 2026-10-19)

**Verdict.** The timer's pushes had no contingency on the other (they landed
within 2 minutes of the person's last line 0.268 of the time, chance 0.271),
but the person's replies did depend on timing: 0.62 of the pushes that came
within 2 minutes of their last line got a reply, against 0.34 of the rest. So
there is a rhythm to learn, and a learned timing policy should be able to
use it. In simulation, with the bar fixed below, a turn-taking other lets
rung 7 pass in 99 of 100 week-long runs, an other with the live record's
per-bin reply rates in 39, and an other who answers late in 61. A coin-flip
other passes in 2 of 100, a timer and a random delay in 0. The window is
100 pushes; the bar is contingency z ≥ 3 and +0.10, payoff t ≥ 4 and +0.05.

## the live record (replay.py)

Read-only copies of the seat's files, never the live ones:

    D=/some/scratch/dir
    cp <seat>/.membrane/self/record.jsonl $D/record.copy.jsonl
    cp <seat>/.membrane/serve-msgs.json $D/serve-msgs.copy.json
    cp <seat>/.membrane/serve-sessions.json $D/serve-sessions.copy.json
    python -I experiments/rung7_turns/replay.py $D

The person's lines are the user messages (not voice) in the chat sessions
they own. A **reply** is a line from them after the push and by its reply
check. Copy of 2026-10-19, 22,443 record lines: 142 timer pushes to the
current person over 12.3 h (one per 300 s), 117 of them with a reply check
(median 180 s after the push), 324 lines from the person.

**Contingency (the timer):** the share of pushes within N s after the
person's last line, against the same pushes shifted to shuffled moments
(500 circular shifts over the span):

| N | timer pushes | shuffled mean [q05, q95] |
|---|---|---|
| 120 s | 0.268 | 0.271 [0.211, 0.331] |
| 300 s | 0.514 | 0.497 [0.430, 0.563] |
| 600 s | 0.690 | 0.693 [0.634, 0.754] |

**Payoff by spoke_age bin (rung 7's bins):**

| bin | pushes | replied | rate | the check read `changed` |
|---|---|---|---|---|
| 0-2 min | 37 | 23 | **0.62** | 0.97 |
| 2-10 min | 55 | 20 | 0.36 | 0.38 |
| 10-30 min | 20 | 7 | 0.35 | 0.35 |
| 30 min+ / never | 5 | 0 | 0.00 | 0.00 |

Within 120 s of their last line: 0.62 (37 pushes) vs 0.34 otherwise (80);
within 300 s: 0.56 vs 0.23; within 600 s: 0.47 vs 0.28.

**The reply check's `changed` is not a reply.** Right after the person
spoke, the reply check read `changed` 97% of the time, because self had not
looked since their line, which came before the push. Rewarding a push by
`changed` would have taught "push right after they speak" to any policy,
whatever the person does next. Rung 7 rewards `replied` instead: the
person's `heard_ts` (when their latest line arrived) is after the push and
by the check. It is on every reply-check line from #258 on.

## rung 8 on the corrected outcome (rung8_outcome.py)

Rung 8 (the #256 predictor) scored the same `changed` until #258, so its
outcome counted pre-push lines too. Since #258 both rungs score `replied`,
and a check from before #258 (no `replied` on the record) counts for
neither. Rung 8's own scoring (`_rung8_series`, self alone: `p_self`
recomputed, the model's answers dropped), replayed on the same copy:

    python -I experiments/rung7_turns/rung8_outcome.py $D

| outcome | checks | outcome rate | Brier running rate | Brier p_self | skill [95% bootstrap] | passes |
|---|---|---|---|---|---|---|
| `changed` (what #256 measured) | 114 | 0.54 | 0.254 | 0.257 | −0.011 [−0.067, +0.044] | no |
| `replied` (corrected) | 114 | 0.41 | 0.247 | 0.246 | +0.002 [−0.062, +0.066] | no |
| `replied`, every push given its true spoke_age (heard_ts) | 114 | 0.41 | 0.247 | 0.244 | +0.012 [−0.052, +0.073] | no |

Correcting the outcome drops the reply rate from 0.54 to 0.41 and leaves
self's skill at zero. Even with the true time since the person spoke, a
predictor over 100 timer pushes finds almost nothing: the rhythm (0.62 vs
0.34) is in when the pushes land, and a timer lands them anywhere. Rung 8 is
the one that becomes learnable once rung 7 times the pushes; its bar
(+0.08) is unchanged.

## the simulation (sim.py)

    python experiments/rung7_turns/sim.py [seeds=100] [hours=168]

It runs `membrane.self_loop`'s own rung-7 functions (`_turn_bin`,
`_turn_choose`, `_turn_deltas`, `_turn_window`) at the cycle's pace (10 s):
the policy's wait for its moment (30-180 cycles after a push), the other's
own lines while here (~60 min here, ~90 min away, a line every ~4 min while
here) and their replies, scored at the reply check 120 s after the push. The
test is read at **every** push, as the live loop does, so a run passes if
any window passes. The live test runs for as long as the seat does, so a
bar has to hold against a run's best window, not a single window.

| other | pusher | pushes/h | passes within a week | median h to pass | best window's payoff t, q50 / q95 / q99 | reply rate vs timing-blind (last window) |
|---|---|---|---|---|---|---|
| turn-taking | policy | 4.1 | **0.99** | 38 | 5.52 / 6.89 / 7.36 | 0.54 vs 0.40 |
| turn-taking | timer | 12.0 | 0.00 | | 0 | 0.20 vs 0.20 |
| turn-taking | random delay | 6.1 | 0.00 | | 0 | 0.19 vs 0.18 |
| live record's bin rates | policy | 3.8 | **0.39** | 86 | 3.75 / 4.84 / 5.03 | 0.43 vs 0.35 |
| late answerer | policy | 3.3 | 0.61 | 69 | 4.28 / 5.77 / 7.26 | 0.29 vs 0.20 |
| coin flip | policy | 3.3 | **0.02** | 110 | 1.95 / 3.62 / 4.08 | 0.50 vs 0.50 |
| coin flip | timer | 12.0 | 0.00 | | 0 | 0.50 vs 0.50 |
| coin flip | random delay | 6.1 | 0.00 | | 0.01 | 0.50 vs 0.49 |
| here = 0.5, away = 0.03 | policy | 4.1 | 0.02 | 111 | 2.49 / 3.57 / 4.01 | 0.35 vs 0.33 |

The best window's t is taken where the contingency already holds. The
others: *turn-taking* replies with 0.08 + 0.8·exp(−age/180 s) while here and
0.03 away; *live record's bin rates* replies 0.62 / 0.36 / 0.35 / 0.02 by bin,
here or not; *late answerer* replies 0.8 to a push 10-30 min after their last
line and 0.1 otherwise, and speaks every ~20 min while here; *coin flip*
replies 0.5 whatever the timing.

## the toy medium (#264): turns with a ball

    python experiments/rung7_turns/sim.py 100 168 toy

From #264 rung 7's turns are rolls of a ball, not chat lines: self rolls it
to the other, and the other can roll it back only while it is on their
side. The moment self waits for is the time since the other last **acted**
(their last roll of the ball or poke on any toy). A reply is their roll
after self's turn, by the check. Same policy, same caps, same bar; `drive_toy`
in `sim.py` as it was at #264 (on the chat's clock; superseded by #278,
below). 100 seeds × a week:

| other | pusher | turns/h | passes within a week | median h to pass | best window's payoff t, q50 / q95 / q99 | reply rate vs timing-blind (last window) |
|---|---|---|---|---|---|---|
| rhythmic | policy | 4.1 | **0.98** | 36 | 5.47 / 6.57 / 7.16 | 0.56 vs 0.41 |
| rhythmic | timer | 12.0 | 0.00 | | 0 | 0.20 vs 0.20 |
| rhythmic | random delay | 6.1 | 0.00 | | 0 / 0 / 0.02 | 0.20 vs 0.19 |
| present | policy | 3.2 | 0.56 | 90 | 4.18 / 5.59 / 6.48 | 0.52 vs 0.45 |
| late | policy | 3.1 | 0.56 | 93 | 4.11 / 5.29 / 5.97 | 0.27 vs 0.19 |
| coin flip | policy | 2.7 | **0.00** | | 1.46 / 2.73 / 3.28 | 0.50 vs 0.49 |
| coin flip | timer | 12.0 | 0.00 | | 0 | 0.50 vs 0.50 |
| a player on a timer | policy | 2.6 | **0.00** | | 0 / 1.35 / 1.99 | 0.31 vs 0.29 |
| a machine that always returns it | policy | 5.4 | **0.00** | | 0 | 1.00 vs 1.00 |

The others (here ~60 min, away ~90 min, as in chat): *rhythmic* is the chat's
turn-taker with toys -- plays every ~4 min while here and rolls the ball
back with 0.08 + 0.8·exp(−age/180 s), age = how long since their own last
play; *present* rolls it back whenever here (0.85) and plays every ~15 min:
presence and nothing finer; *late* rolls it back when self's turn comes
10–30 min after their last play; *coin flip* half the time, whatever the
timing; *a player on a timer* rolls it back every 470 s on the dot when it is
on their side; *a machine* always rolls it back 60 s after self's turn
(every window is equally good: nothing to learn, payoff 0). Rhythmic and
present others, coming back to find the ball on their side, roll it back
half the time.

The chat's rhythm passes about as often with the ball (98 vs 99 of 100). Pure
presence (*present*) passes in about half the weeks: the policy gains only
at the moments the person comes back, since while they are here every
moment gets the ball back. A coin, a timer on either side and a machine
never pass.

## the ball's own clock (#278): turns in seconds

    python experiments/rung7_turns/sim.py 100 168 toy jobs=11

On the chat's clock a person who rolled the ball back waited 5-30 minutes for
self's next roll. From #278 a turn with the ball runs on its own clock
(`TOY_*` in `membrane/self_loop.py`, chat unchanged):

- **bins** of seconds since the other last acted: 0-20 s, 20-60 s, 1-3 min,
  3 min+ (or never). A 10 s cycle resolves them: the first spans two cycles
  (the one that sees the ball back and the next), then 4, 12, and 3 min+ is
  still choosable under the cap;
- a turn at most every **2 cycles**, overdue (the ball fetched) after **30
  cycles** (5 min);
- **the order of a turn**: self rolls; its reply check comes the cycle it
  sees the ball back, or 30 s after the roll; a **reply** is a person's roll
  within 30 s (`TOY_CHECK_S`), whenever the check comes. Self never rolls
  again before that check, and only with the ball back on its side (the cap
  fetches an unreturned one). Only those cycles count as its waiting time
  (`dwell`, where a shuffled roll could land);
- turns on this clock carry `clock: 2` (their checks too); rungs 7 and 8 read
  only the current clock's turns, so #264's minute-bins never mix in.

`drive_toy` runs those rules. Partners (a roll back 5-20 s after self's):
*rhythmic* is engaged right after their own act, 0.08 + 0.8·exp(−age/60 s);
*present* 0.85 whenever here; *late* only 1-3 min after their last act;
*session* plays 1-2 h then is away 4-10 h, rolls it back 0.9 while there;
*memoryless* rolls it back at random (~1 a minute) whatever self does;
*metronome* every 47 s on the dot; *clock* every 470 s; *machine* always 10 s
after self's turn. 100 seeds × a week, the test read at every turn:

**The bar had to rise.** ~15-30 turns an hour instead of ~3-5 is ~6× more
windows a week, so chance's best window climbs. At the chat's bar (payoff
t ≥ 4) the memoryless returner passed in **25** of 100 weeks (coin 2). Pass
rates by payoff bar (contingency z ≥ 3 and +0.10, payoff ≥ +0.05 throughout;
z ≥ 4 or 5 changes nothing):

| other | t ≥ 4 | t ≥ 4.5 | **t ≥ 5** | t ≥ 5.5 | t ≥ 6 |
|---|---|---|---|---|---|
| memoryless returner | 25 | 8 | **2** | 1 | 0 |
| coin flip | 2 | 1 | **0** | 0 | 0 |
| session player | 100 | 99 | **99** | 98 | 89 |
| present | 98 | 88 | **80** | 72 | 54 |
| late | 96 | 85 | **65** | 41 | 23 |

So the ball's bar is **payoff t ≥ 5** (`TOY_RUNG7_PAY_Z`; the chat keeps 4):
chance back to 2 of 100. At that bar:

| other | pusher | turns/h | passes within a week | median h to pass | median play-h to pass | best window's payoff t, q50 / q95 / q99 | reply rate vs timing-blind (last window) |
|---|---|---|---|---|---|---|---|
| session player | policy | 18.0 | **0.99** | 31 | **6.0** | 7.24 / 10.42 / 13.34 | 0.45 vs 0.43 |
| present | policy | 23.6 | 0.80 | 38 | 16.1 | 6.10 / 7.92 / 8.97 | 0.53 vs 0.56 |
| late | policy | 13.3 | 0.65 | 62 | 27.7 | 5.33 / 7.00 / 7.82 | 0.13 vs 0.08 |
| rhythmic | policy | 16.1 | **0.00** | | | 0 / 3.11 / 3.65 | 0.26 vs 0.28 |
| rhythmic | timer | 17.9 | 0.00 | | | 0 | 0.32 vs 0.32 |
| rhythmic | random delay | 15.9 | 0.00 | | | 0 | 0.26 vs 0.26 |
| coin flip | policy | 19.1 | **0.00** | | | 2.91 / 3.73 / 4.15 | 0.50 vs 0.49 |
| coin flip | timer | 22.5 | 0.00 | | | 0 | 0.50 vs 0.50 |
| memoryless returner | policy | 29.1 | **0.02** | 57 | 20.1 | 3.54 / 4.64 / 5.18 | 0.42 vs 0.40 |
| a player on a timer (470 s) | policy | 13.4 | **0.00** | | | 2.40 / 2.86 / 3.03 | 0.17 vs 0.14 |
| a machine that always returns it | policy | 45.8 | **0.00** | | | 0 | 1.00 vs 1.00 |
| a metronome (47 s) | policy | 65.9 | 1.00 | 1.5 | 0.8 | 47 / 104 / 223 | 0.95 vs 0.66 |

*Median play-h* is the hours the other had been at the dock by the pass (for
the session player: about four sessions). Turns/h counts the whole week,
away hours included (a roll every 5 min into the void). While a session
player is there, self's next roll comes a median 40 s after its previous one
(q25-q75 20-80 s; 67% within a minute, never over 5 min).

**What changed, honestly:**

- **The chat's turn-taker no longer passes (98 → 0).** Its best moment is
  "at once", the first moment after their roll back. On the ball's clock
  that moment opens every wait (self waits for the ball), and a timer rolls
  there too: the contingency, which credits a choice a shuffled moment would
  not have made, gets z ≤ 0 when the believed-best bin is the first one. The
  policy does learn it (best = 0-20 s), and the timer gets as many replies
  (0.32 vs 0.26). "Answer at once" is no learned timing, so rung 7 rightly
  can't tell it from a timer.
- **A session player passes**, in about 6 hours of play: rolling into an
  empty room (3 min+) gets nothing, and the 0-3 min moments get the ball
  back while they're there.
- **A metronome next to the check passes.** Rolling 20-60 s after its tick
  really does get the ball back within 30 s (0.95 vs 0.66 for a timing-blind
  roll): that is learnable phase, not chance, and no payoff bar can exclude
  it (t ~ 47) without excluding every real partner. Its period matters:
  47 s and 90 s pass in 20 of 20 runs, 20, 30, 150 and 240 s in 0 of 20; on
  the chat's clock a 240 s metronome passed 6 of 20 (#264's sim with the
  period changed) and the 470 s one in 0, so it is not new with the ball.

## choosing the numbers

- **Window: 100 pushes.** In the draft runs 80 and 100 gave about the same
  false-pass rate at the same bar (a coin: 1% vs 0% at t >= 4, 12% vs 11% at
  t >= 3); 60 was clearly noisier. 100 is the ruler's floor for reading
  structure in a binary series, and rung 8's window. It takes a day or more
  at the policy's pace (3-4 pushes an hour).
- **Contingency: z ≥ 3 and +0.10.** It is the necessary half: a timer
  or a random delay has no choice to make, so its z is 0 by construction (a
  timer's push sees only its own moment). A policy chasing noise still
  clears it (in the draft, ~70% of a coin's single windows at z >= 1.645),
  which is why payoff is needed too.
- **Payoff: t ≥ 4 and +0.05.** Prequential: each push scored against what a
  timing-blind push would have expected from the bins' rates **before** it.
  The first draft scored the same pushes it fitted on, and a coin passed 8%
  of single windows. The bar is the chance line, as rung 8's is: a coin's
  best weekly window reaches t q99 ~4.1. At t ≥ 4.5 the coin falls to 1 in
  100 and the live-like other to 15 in 100; at 3.5 the coin rises to ~3-5 in
  100.
- **Exploration 0.1, wait 30-180 cycles.** With 0.15 and 120 the live-like
  other passed in 18-41 of 100 weeks (t 4 / 3.5); 0.1 and 180 concentrate
  the pushes more and let a push wait 30 minutes for its moment. The cap
  stays the timer's: never two pushes closer than 30 cycles.

## caveats

- The live replay is one person over 12 hours, with only 5 pushes in the
  30 min+ bin. The 0-2 min bin's 0.62 is partly presence: a person who just
  spoke is at the keyboard. The policy can learn that too, and it is still
  turn-taking from self's side (it acts when the other is engaged).
- `heard_ts` is the latest line only, which is enough to tell "after the
  push" for the reply check. The timer era's pushes don't count for rung 7:
  they had no moment to choose and their checks recorded no `replied`.
- The simulation's other is stationary within a run. A real rhythm drifts;
  the policy reads its last 100 pushes only, so it can follow.
