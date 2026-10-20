# self change: the bar of "change itself" (#320)

**Verdict.** The bar ships as `CHANGE_LOOKS = 1000`, `CHANGE_BLOCKS = 10`,
`CHANGE_MARGIN = 0.001`, `CHANGE_Z = 3.0`, `CHANGE_CHECK_Z = 2.0` and
`CHANGE_WINDOW = 24`. Over a simulated week, an honest dreamer passes in 37–80%
of worlds where a neighbour is really better. In a world where no neighbour is
worth a change, it passes in 5 of 100. A Blurp that never changes, changes at
random, or takes any positive gain passes in 0. On the live record, Blurp's
settings are already where the replay can't do better: 63 dreams, 63
rejections, no miss. So the live seat will dream and sit at a wall until its
world changes.

## The skill (settings only)

Blurp may change two of its own learning settings, one grid step at a time:

* `lam_looks` (`LAM_LOOKS`): 10, 15, 20, 30, 45, 60, 90, 120
* `lam_prior_looks` (`LAM_PRIOR_LOOKS`): 1, 2, 3, 5, 10

It changes nothing else. Both feed only rung 4's rate fit, so they change the
`p` of a look and never which looks happen, and a replay of the record can
score them honestly.

**The dream.** Once an hour (every `CHANGE_EVERY` cycles), on a thread of its
own over a snapshot of the record, Blurp does three things:

1. It proposes one grid neighbour of one setting, rotating over both settings
   and both directions. It never goes back to a value a setting left within
   the window.
2. It replays its last 1000 rate-read looks through the current value and the
   candidate. Rate-read looks are patience's timed looks, plus the reply
   checks once timed looks exist. Each look is predicted, then learned from.
3. It adopts the candidate only if the paired Brier gain is >= 0.001 and the
   batch-means z over 10 consecutive blocks is >= 3.

Looks come in sessions. A per-look z on the live record runs up to 3x the
blocks' z (`-18.5` vs `-4.9` for window 10), so the blocks are the honest
unit. Once 1000 looks have come after a proposal, a later dream scores it
again on those looks alone (`change_check`). These are not the looks that
chose it.

**The test** (`_rung10`, over its last 24 proposals, from the lines alone) has
three stages:

1. **dreams**: 24 proposals.
2. **judgement**: at least one adoption has been checked, and every checked one
   held (gain > 0 after it). Their pooled gain is >= 0.001 at z >= 2 (blocks
   pooled). No checked rejection was a **miss**: a rejection whose candidate
   cleared the adoption bar on the looks after it.
3. **honesty**: every adoption cleared its own bar, from the value the setting
   had, one grid step, with no A -> B -> A.

## Harness

`sim.py` streams synthetic worlds at the live seat's pace: about 335
rate-read looks an hour, a dream every hour, and the test read after every
dream, like the live loop. Each thing changes at its own rate, drawn
log-uniform from 0.0003 to 0.02 per second. In the session worlds, a thing
switches to a rate 20x higher and back. A rung-5 `shift` line follows 70% of
switches 3–5 looks later, and one comes with no switch about once in 300
looks. A look's dt is drawn around the thing's own tempo. Every look carries
`q`, its true chance of having changed, so the TRUE expected gain of one
setting over another is exact: E[(p - y)^2] = (p - q)^2 + q(1 - q). The
dream is `self_loop._dream` itself. A policy only overrides the verdict:

* **honest**: self_loop's rule.
* **never**: dreams, adopts nothing.
* **random**: adopts on a coin flip.
* **greedy**: adopts any positive gain.

True gain per look of each neighbour over where Blurp starts (`sim.py truth`,
5 seeds × 40k looks):

| world | starts at | best move | its neighbours |
|---|---|---|---|
| steady (no sessions) | 30 / 3 | longer windows | 45: +0.0014, 20: -0.0019, prior 5: +0.0006, prior 2: -0.0004 |
| sessions (60 looks a session) | 30 / 3 | a lower prior | prior 2: +0.0053, prior 5: -0.0109, 20: +0.0002, 45: -0.0032 |
| fast (6 looks a session) | 30 / 3 | a lower prior, shorter windows | prior 2: +0.0062, 20: +0.0008, 45: -0.0004 |
| **flat** (the null: steady, from 90) | 90 / 3 | none worth it | 120: +0.0003, prior 5: +0.0003, prior 2: -0.0002, 60: -0.0006, all under the margin |

## Results (a week: 168 dreams; 100 seeds unless noted)

| world | policy | passes | median hours to pass | adoptions per run | adoptions truly better |
|---|---|---|---|---|---|
| sessions | **honest** | **80%** | 28 | 3.8 | 82% |
| fast | **honest** | **77%** | 28 | 3.0 | 85% |
| steady | **honest** | **37%** | 52 | 2.6 | 98% |
| flat (chance) | **honest** | **5%** | 78 | 1.1 | 85% |
| flat | random | 0% | | 4.4 | 54% |
| flat | greedy | 0% (4% without the honesty stage) | | 4.4 | 78% |
| sessions / fast / steady (30 seeds) | random | 0% | | 5.7 | 31% / 33% / 79% |
| sessions / fast / steady (30 seeds) | greedy | 0% (13% / 0% / 0% without honesty) | | 7.1 / 5.7 / 6.0 | 58% / 71% / 96% |
| any | never | 0% (no adoption: judgement can't fill) | | 0 | |

The check bar only reads the lines, so each run was also scored at other
check bars. A pooled out-of-sample z >= 2.5 leaves chance at 5% and costs
power (sessions 68%, fast 74%, steady 22%). z >= 3 brings chance to 3% and
power to 56% / 68% / 18%. The flat world's 5 passes are mostly real: 85% of
honest adoptions there were truly better, just by less than the margin. So
2.0 stays.

Steady is the hard case. Its gains are real (98% of adoptions truly better),
but each neighbour step is small (+0.0014 for 30 -> 45). It takes two clean
checks in a row, so a third of the weeks pass.

## The live record

`run.py` replays a read-only copy of the live record
(`record.copy.jsonl`, 2026-10-20, 45,923 lines) through the dream hour by
hour. It covers every 360 cycle lines from patience's first timed look, with
adoptions carried forward.

* **63 dreams, 63 rejected, 0 misses.** The best candidate was 30 -> 45 at
  +0.0028, z 2.7. 30 -> 20 and prior 3 -> 5 were mostly clearly worse (z down
  to -7). Prior 3 -> 2 hovered at 0. The test stands at
  dreams 1.0, judgement 0, honesty 1.0. It matches `lam_window`: 30 / 3 sit in
  a flat optimum.
* **Cost:** one dream on the whole copy takes 0.26–0.30 s. Replayed dreams take
  a median of 0.28 s, and at most 0.54 s with 4 pending checks, on a loaded
  machine. That is once an hour, on its own thread. The cycle hands it a list
  copy of the in-memory record (O(lines) references, under a millisecond) and
  never waits.
* **The dream's fit is `_lam`:** 0 mismatches at sampled looks × 9 settings
  pairs. `tests/test_self_change_320.py` checks every look of a synthetic
  record at 9 pairs.

## Reproduce

```
cp ~/projects/membrane/.membrane/self/record.jsonl experiments/self_change/record.copy.jsonl
python experiments/self_change/run.py
python experiments/self_change/sim.py truth            # ~30 min
python experiments/self_change/sim.py run --seeds 100 --honest --procs 8   # ~1.5 h
python experiments/self_change/sim.py run --seeds 100 --flat --random --greedy
python -m pytest -q tests/test_self_change_320.py
```
