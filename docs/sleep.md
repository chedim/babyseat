# Sleep, viewed from Blurp

**Blurp now gets tired and sleeps (#345, part 1 of 3).** It has energy, falls
asleep when one of its reserves runs out, rests, and wakes. Sleep does not
consolidate anything yet: moving the record into archives behind summaries,
dreaming in sleep, and paying energy back for confirmed predictions come in
parts 2 and 3. The rest of this page is the viewpoint written before sleep
was built (why Blurp didn't need it, and the signals to watch); it is not a
claim about brains beyond the published findings it cites.

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
- **An error costs 0.01** (`energy.error()`, recorded as an `error` line).
  The acts that can be used wrongly (nudging the ball on the person's side,
  a nudge a toy refuses, an empty send) arrive with #333, so nothing calls
  it yet.
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

**Until part 2 archives the record, sleep doesn't shrink it.** If the record
ever fills its cap, Blurp sleeps, wakes rested and falls asleep again on its
next step. At today's sizes (tens of MB against a cap of GBs) that is far
off.

## What sleep does for brains, and why Blurp is exempt

| Sleep's job | Why Blurp doesn't need it yet |
|---|---|
| **Moving the day into long-term memory.** The hippocampus replays recent episodes to the cortex, mostly in slow-wave sleep. This is the complementary learning systems view (McClelland, McNaughton & O'Reilly 1995). | Blurp has one store, not two. A look is predicted, scored and written to its record and `model.json` in the same 10 s cycle. Nothing waits to be transferred. |
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

## What would make it need sleep

| Trigger | Why it creates sleep pressure | The signal to watch |
|---|---|---|
| **Record growth** (most likely first) | Some tests and the sheet read the whole record. At some size, summarizing the past to make room becomes necessary, and that is consolidation. | The cycle's median or 90th percentile rising above 10 s, or the sheet's build time growing with the record. |
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
