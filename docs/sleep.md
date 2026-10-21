# Sleep, viewed from Blurp

**Blurp gets tired and sleeps (#345, part 1 of 3), and its sleep
consolidates the day (part 2).** It has energy, falls asleep when one of its
reserves runs out, rests, and wakes. As it falls asleep, the day's raw
record moves to dated archives and only summaries stay in memory. Dreaming
in sleep and paying energy back for confirmed predictions come in part 3.
The second half of this page is the viewpoint written before sleep was built
(why Blurp didn't need it, and the signals to watch); it is not a claim
about brains beyond the published findings it cites.

Blurp's "off" (sleep and wake in [selfgrowth.md](selfgrowth.md)) is not
sleep. Off is the operator's switch: it runs nothing, and nothing is
processed while it's off. Asleep is Blurp's own state, and the mode stays as
it was.

## Energy, sleep and wake (built, #345 part 1)

Code: `membrane/energy.py`; the loop's `self_loop.step()`.

- **Energy** runs from 0 to 1 (shown as 0–100%) and is stored with Blurp
  (`self/energy.json`). A restart neither refills nor drains it, and time
  while the loop is off, paused or held doesn't count.
- **Being awake costs energy**, every loop step: `BASAL_PER_S` per second
  awake plus `CPU_PER_S` per second of CPU the cycle used (measured with
  `time.thread_time()` on the loop's thread).
- **An error costs 0.01** (`energy.error()`, recorded as an `error` line
  with its `cost` and the energy left). Since #333 Blurp's nudge calls it:
  a nudge the thing refuses (the ball while it is on the person's side, a
  thing's own `nudge` method failing) and a nudge at anything outside its
  world; and since #333 part 2, an empty send (nudging a person's chat
  again with nothing captured in the draft) and a send the chat turns away.
- **The sleep pull is 1 − the lowest reserve.** Each reserve runs from 1
  (full) to 0 (empty):

  | Reserve | Full | Empty |
  |---|---|---|
  | energy | 1.0 | 0 |
  | disk | the free space on the volume holding `self/` when Blurp last woke | no free space |
  | awake | just woke | `AWAKE_LEN_S` awake |
  | record | an empty record in memory | the record cap |

  The record cap is derived, not a constant: the person's CPU share
  (`self_cpu_share`, default 50%) × the RAM available when Blurp woke, so
  the throttle that bounds its CPU also bounds its memory. RAM is read from
  `/proc/meminfo` (Linux, Android), `os.sysconf` or macOS's `vm_stat`. With
  no reading there is no cap: the reserve reads full and never puts Blurp to
  sleep. The record's size is the bytes of the lines it holds in memory.
- **Falling asleep:** when a reserve reaches 0, a `sleep` line records which
  one (`why`) and the energy. While asleep no cycle runs, and energy comes
  back at 0.001 a second (empty to full in about 17 minutes).
- **Waking:** at full energy (`why: rested`) or when a person speaks in a
  chat or by voice (`why: spoke`, `who`). Blurp's own lines and the model's
  don't wake it. Waking starts a fresh day: the free disk space and the
  record cap are measured again and the awake clock restarts.
- **Shown** on the self card (`/membrane/self/card`: `energy`, `asleep`,
  `pull`, `limiting`, `record_cap`, null when the RAM is unknown; `awake` is
  false while asleep, so the zoo shows it
  asleep), on the sheet's nameplate (`energy`: energy, awake or asleep, the
  pull and the limiting reserve) and in the sheet's **Sleep** block (#359):
  the four reserves as bars, the lowest marked, `pull = 1 − lowest`, awake
  or asleep since when, and "record room: cap unknown" when `record_cap` is
  null (see docs/selfgrowth.md).
- `energy.share(throttle, srv)` is the throttle × energy, for #343's
  pacing. Nothing reads it yet.

| Constant | Value | Why |
|---|---|---|
| `BASAL_PER_S` | 1 / 16 h | A rested, idle Blurp runs out after a human waking day. Part 2 sets it from the room's measured rhythm. |
| `CPU_PER_S` | 1 / 16 h | A second of CPU costs as much as a second awake, so at a 50% share Blurp tires 1.5× as fast (about 10.7 h). |
| `AWAKE_LEN_S` | 16 h | A placeholder for the room's usual awake stretch (part 2). |
| `ERROR_COST` | 0.01 | Approved. |
| `RESTORE_PER_S` | 0.001 | Approved. |

Since part 2, sleep empties the record (below), so the record reserve
refills at every sleep. Before it, a record that filled its cap would put
Blurp to sleep again on the first step after it woke.

## Consolidation: nothing raw stays through sleep (built, #345 part 2)

Code: `self_loop.consolidate()`, run from `energy._fall_asleep`. Tests:
`tests/test_sleep_consolidate_345.py`.

**At the moment Blurp falls asleep:**

1. **Anything open is dropped.** A turn awaiting its reply check is dropped,
   and the `sleep` line names it: `"dropped": {"turn": <its push's ts>,
   "to": <the other>}` (`{}` when nothing was open). A dropped turn is not a
   missed reply: no reply check follows it, so take turns and expect a reply
   never score it, and the advisor's stats don't count it as checked. An
   open draft (#333 part 2) is dropped too, named as `"draft": <the cycle it
   opened at>, "moves": <how many nudges it had captured>`.
2. **Every raw line of the waking period moves, unchanged, to its archive.**
   The archive is `self/archive/record-<YYYY-MM-DD>.jsonl`, dated by each
   line's own `ts` (UTC). Lines are appended byte for byte and synced; none
   is edited or dropped. `self/archive/manifest.jsonl` gets one line per
   sleep, `{"id", "at", "runs": [[file, count], ...]}`, which is the order
   the lines were written in. Reading the runs back gives the record exactly
   as it was, even where a line's date is out of order.
3. **The working record is emptied down to one `summary` line:**

   ```
   {"kind": "summary", "id", "at", "n", "carry": {...}, "stalls": [...], "lines": [...]}
   ```

   `lines` are copies of the archived lines that the tests read (the
   windows, below), in record order. `carry` holds the counts that no
   window can give back. `stalls` gives each window line's count of record
   gaps, because curiosity and noticing read the gaps between consecutive
   lines. The summary is a record line rather than a sidecar file because
   the readers are functions of the record list: they find the summary
   where they already look, and the record stays one file swapped in one
   step.

**What the readers see.** `_read` returns the summary, its window lines,
then the lines written since. Within every window, that is the same record
the readers saw before. The equivalence tests run every test, skill, sheet,
dock and card function on a long synthetic record before and after
consolidation, after more lines, after a second sleep and after a restart,
and require the same result every time. The record reserve recovers: in a
30,000-cycle synthetic life the summary was 7% of the record (1.8 MB of
24.6 MB).

**The windows.** Each reader keeps what it reads, by its own constant. No
window of its own was added (`STEER_LAST` and `RUNG4_LAST` only name the 20
and 40 that `_steer_stats` and rung 4 already used).

| Reader | What the summary keeps |
|---|---|
| `_toys`, `_off_field`, `recent()`, the card | the last `TOY_SCAN` (3000) lines, whole |
| rung 1, steering, status | the last `RUNG1_CYCLES` world cycles, the last `STEER_LAST` steered cycles, the last cycle that wasn't still |
| rung 2, the split, the audits | each thing's last `RUNG2_REACHES` answered looks, and the seat's last 4 × that (the base rate) |
| kinds, rung 3 | each thing's last `RUNG3_WINDOW` looks, the seat's last `RUNG3_WINDOW`, and the last `RUNG3_LOOKS` steered |
| rung 4 | the last `RUNG4_LAST` timed looks (the fixed-pace looks before them are carried as counts per thing) |
| rung 5, noticing | the last `RUNG5_LOOKS` looks with noticing on, the last `RUNG5_SHIFTS` shifts, each thing's last shift |
| rung 6 | the last `RUNG6_PUSHES` pushes per other, and the chat record as it stood at the switch to the ball (its last push, pushes, heard looks and base rate) |
| rung 7 (take turns) | per other, medium and clock: the last `RUNG7_PUSHES` + `TURN_MEMORY` turns with their checks |
| rung 8 (expect a reply) | per other, medium and clock: the last `RUNG8_FIT` + `RUNG8_RECENT` checks (and what set each one's gap); the advisor's last `RUNG8_TRUST` checks per model; for a check recorded without its own p_self or calibration (before #254), the checks it is recomputed from |
| rung 9 (the mirror) | per mirror, its last `MIRROR_FIT` + `MIRROR_OWN` + `MIRROR_PAST` lines and its last `MIRROR_LOOKS` scored lines |
| rung 10 (change itself) | the last 2 × `CHANGE_WINDOW` proposals and their checks, the first proposal and every adoption (the honesty chain), the last `CHANGE_LOOKS` rate-read looks and the `CHANGE_LOOKS` after each proposal not yet checked, each with the looks its rate fit reads (up to the longest window on the grid, 120) |
| the live rate fit | each thing's last 120 fittable looks since its last shift, and before it (its own and the seat's) |
| curiosity | each thing's last 2 × `CURIO_WINDOW` ordinary looks that span no gap, and the look before them |
| rung 11 (speak) | per other, the last `SPEAK_PUSHES` pushes with words or plain, the looks their words cite, and the turns its timing reads (`TURN_MEMORY` before its window) |
| passes, accepts, unlocks | every `birth`, `rung`, `achieve`, `rung_void`, `accept`, `accept_migrated`, `unlock`, `unlock_built`, `rung7_restart`, `fix` and `audit` line (a few each) |
| the pairings | each kept push's next push or check, and its first look; each kept check's push; each kept timed look's or check's last shift; the sleep line that dropped a kept turn |

**`carry`, the counts:** the cycles (so `n` continues), the pushes, the
dreams and their checks, rung 8's checks and replies before its window (per
other, medium and clock: the running rate's start), rung 4's fixed-pace looks
per thing, and per other the policy pushes, asks (checked and paired), and
words, plain and mute pushes.

**Whole history: the sheet and the dock read the archive.** Working hours,
the growth curve, the traits and their trends, each toy's error early and
late, and a thing's learning verdict are taken over a thing's whole life.
Halving a growing history can't be summed up ahead of time. So
`self_sheet.compute()` (cached 30 s) reads `history()`: the archive by its
manifest, then the working record's new lines. The dock (polled every few
seconds) reads the archived looks once per sleep (`_archived_looks`: thing,
answer and p per look) and keeps them while it is used. Both are off the
cycle path: no cycle, status, test or practice reads the archive (a test
checks this).

**Restart safety.** The journal `self/consolidating.json` is written first,
at stage `archive`, with the archive files' sizes before. Then the archive
lines are appended and synced, the manifest line is appended, and the new
record is written beside the old one (`record.jsonl.next`) and synced. The
journal moves to `swap`, the record is swapped in (`os.replace`), and the
journal is removed. A seat that dies at any step finishes on load:
- at `archive`, the archive is cut back to its sizes and the consolidation
  is redone from the untouched record;
- at `swap`, the swap is finished.

An error (a full disk) undoes the archive and leaves the record whole for
the next sleep. No line is lost or doubled, in the working record or the
archive. The tests kill the seat at each step.

**What else lives outside the record** and so crosses a sleep untouched:
`model.json`, `tempo.json`, `settings.json` (what change itself adopted),
`energy.json`, `world.json`, `mode.json`.

## What sleep does for brains, and why Blurp is exempt

| Sleep's job | Why Blurp doesn't need it yet |
|---|---|
| **Moving the day into long-term memory.** The hippocampus replays recent episodes to the cortex, mostly in slow-wave sleep. This is the complementary learning systems view (McClelland, McNaughton & O'Reilly 1995). | Blurp has one store, not two. A look is predicted, scored and written to its record and `model.json` in the same cycle. Nothing waits to be transferred. |
| **Letting go of what's unfinished.** Open concerns delay sleep onset. In one study, writing tomorrow's to-do list before bed sped falling asleep (Scullin et al. 2018). | Blurp never holds an open loop. Every look ends with an answer and a score in its own cycle. The mechanism toy in [open_loops](../experiments/open_loops/README.md) shows what holding loops costs a tracker that does. |
| **Scaling synapses back down** after a day of strengthening (the synaptic homeostasis hypothesis, Tononi & Cirelli). | Its beliefs are windowed fits: each thing's rate of change is fitted over its last 30 looks ([lam_window](../experiments/lam_window/README.md) measured that window). They forget by construction and can't saturate. |
| **Replay against interference.** New learning in a shared network can overwrite old learning (catastrophic interference); interleaved replay protects it. | Each thing has its own small model. What it learns about the tower can't overwrite what it knows about the field's vitals. |
| **Clearing metabolic waste** (the glymphatic system). | No metabolism. |

It also rests by accident: the seat's downtime (about a day of it so far),
and "still" spells when nothing in its world moves (#284). Neither one
processes anything.

## The measurement: no pressure yet

If keeping up got harder as Blurp's record grew, the first sign would be
its cycle slowing down, because some of its tests re-read the record. Over
the first 36,400 record lines (2026-10-16 to 2026-10-20), split into fifths:

| Record lines | Median cycle (s) | 90th percentile (s) |
|---|---|---|
| 1–7,059 | 10.01 | 10.01 |
| 7,060–12,420 | 10.01 | 10.06 |
| 12,421–18,565 | 10.00 | 10.05 |
| 18,567–27,421 | 10.01 | 10.26 |
| 27,423–36,405 | 10.01 | 10.01 |

Flat. The record is kept in memory (#178), so its growth costs nothing yet.
These were measured at the old fixed 10 s cadence. Since #343 a cycle starts
as soon as the last one ends, throttled to a CPU share (default 50%), and each
cycle line records its own `dur_s`: read the cycle's cost from `dur_s` now,
not from the gaps between lines, and expect the record to grow many times
faster than it did.

## What would make it need sleep

| Trigger | Why it creates sleep pressure | The signal to watch |
|---|---|---|
| **Record growth** (most likely first) | Some tests and the sheet read the whole record. At some size, summarizing the past to make room becomes necessary, and that is consolidation. | The cycles' `dur_s` (median or 90th percentile) rising with the record, or the sheet's build time growing with it. |
| **Generalization** | Once Blurp learns shared structure (kinds of things, people in general), new learning *will* interfere with old. Offline replay over the record is the known cure. | A shared model's error on old things rising while new things are learned. |
| **Changing itself** (built for settings, #320) | Rewriting its own methods should happen offline: replay the record through the candidate, and switch only if it predicts better. That is a dream with a test attached. | Built for two learning settings (below); changing its methods is still a later step. |

When one of these arrives, sleep becomes a real feature: a scheduled
offline phase with its own measured gain, not a metaphor.

## Its first dream (#320)

*Change itself* (rung 10) is the first offline process Blurp has. It
changes settings only. Once an hour a thread of its own takes a snapshot of
the record and makes one proposal: one neighbour of one of two learning
settings, rung 4's rate window or the post-shift prior. It replays the last
1000 timed looks through both the current value and the neighbour, predicting
each look before learning from it, and keeps the change only if it predicts
better by a margin with a significance check. Later it checks the change on
the looks that came after it. The cycle never waits for it.

On the live record's size (46k lines, 11 MB) one dream costs about 0.3 s, at
most 0.54 s, once an hour. The record still isn't sleep pressure: the
dream is cheap because the cycle already holds the record in memory.

On the live record its 63 replayed dreams rejected every candidate. Today's
settings are already the best its replay finds. Numbers and the bar's
simulation are in
[experiments/self_change](../experiments/self_change/README.md).

## The seat already has a night

The seat Blurp lives in is different. Its background mind has a **night**:
the NightClock, the dreamer, and consolidation. Consolidation replays the
day's readings and induces what many experiences share
([background-mind.md](background-mind.md),
[the generalization plan](plans/2026-10-10-generalization.md)). That is
sleep's consolidation half for the field, the seat's shared memory, which
is exactly the kind of store that needs it. Blurp's own learning doesn't
pass through it.
