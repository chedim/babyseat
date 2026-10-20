# Sleep, viewed from Blurp

**Blurp doesn't sleep, and so far it hasn't needed to.** This page says why,
what would change that, and the signal to watch for each change. It is a
viewpoint with one measurement in it, not a model of sleep: nothing here is
a claim about brains beyond the published findings it cites.

Blurp's "off" (sleep and wake in [selfgrowth.md](selfgrowth.md)) is not
sleep. Off runs nothing, and nothing is processed while it's off.

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
| **Changing itself** (a later skill) | Rewriting its own methods should happen offline: replay the record through the candidate, and switch only if it predicts better. That is a dream with a test attached. | The skill itself: "change itself" needs a replay harness before it can be built. |

When one of these arrives, sleep becomes a real feature: a scheduled
offline phase with its own measured gain, not a metaphor.

## The seat already has a night

The seat Blurp lives in is different. Its background mind has a **night**:
the NightClock, the dreamer, and consolidation. Consolidation replays the
day's readings and induces what many experiences share
([background-mind.md](background-mind.md),
[the generalization plan](plans/2026-10-10-generalization.md)). That is
sleep's consolidation half for the field, the seat's shared memory, which
is exactly the kind of store that needs it. Blurp's own learning doesn't
pass through it.
