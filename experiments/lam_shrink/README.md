# lam shrink: self's post-shift rate fit, before and after #235

**Question.** After a rung-5 `shift`, rung 4's `_lam` re-fitted a thing's
change rate from only the looks since the shift (3 to 5 at first). The
Petri-dish lab (branch `exp/petri-dish`, `experiments/petri_dish/README.md`)
found those short fits carry most of self's excess error. Does shrinking the
post-shift fit toward the pre-shift rate fix it?

**Change (in `membrane/self_loop._lam`).** Without a shift nothing changes. After
one, the fit still reads only the looks since the shift, but adds a prior: the
log-likelihood of the thing's last `LAM_LOOKS` looks before the shift (the
seat's looks when the thing has none), plus half a changed and half an
unchanged look at their median `dt` (Jeffreys, so the prior itself is never at
a grid edge), scaled to `LAM_PRIOR_LOOKS = 3` pseudo-looks. At 3 looks the data
and the old rate weigh the same; at 10 the data weighs 77%; at 30, 91%.
`K = 3` was chosen before the replay, from the issue's "data dominates after
~10 looks". The K sweep below is hindsight.

## Data

A read-only copy of the live record (`record.copy.jsonl`, git-ignored, never
committed). The main table cuts it at the Petri lab's end
(`--until 1792373699`, 2026-10-18 21:34 EDT), so the numbers line up with that
lab: 2888 scored looks over 22 things (Petri: 2887), the same 328 short-fit
looks, and the replayed old fit matches the Brier of self's recorded `p` to four decimals (0.1192).

## Harness

The same as the Petri lab's `self_B`. Each thing's answered looks are replayed
outside audits, in time order. The first 30% of each thing's looks are warm-up
and are not scored. On a timed look (and on a reply check once timed looks
exist), `p = 1 - exp(-lam*dt)`. Otherwise `p` is the one bit. `lam` comes from:

* **old**: `_lam` before #235, copied verbatim into `run.py`.
* **new**: `membrane.self_loop._lam` in this tree.

A **short fit** is a look whose rate was fitted over fewer than 10 looks since
the last shift. A **shift** look is a short-fit look that comes after a
recorded shift (the rest are young things that have never shifted).

## Results (Brier per look; lower is better)

| method | overall (2888) | short fit (328) | after a shift (313) | fit >= 10 (2498) | ECE | short: p < .05 or > .95 | ...and wrong | short: lam at grid edge | ...after a shift |
|---|---|---|---|---|---|---|---|---|---|
| self's recorded `p` | 0.1192 | 0.2672 | 0.2723 | 0.0969 | 0.038 | 123 | 44 | - | - |
| **old** `_lam` (replayed) | 0.1192 | 0.2672 | 0.2723 | 0.0970 | 0.038 | 123 | 44 | 108 | 98 |
| **new** `_lam` (K=3) | **0.1056** | **0.1408** | **0.1398** | 0.0979 | **0.021** | 133 | **7** | 18 | 8 |
| dish mix (Petri lab) | 0.1021 | | | | 0.026 | | | | |
| Hedge eta=2 (Petri lab) | 0.1045 | | | | 0.032 | | | | |
| hindsight: K=1 | 0.1050 | 0.1400 | 0.1390 | 0.0972 | 0.019 | 125 | 9 | 18 | 8 |
| hindsight: K=2 | 0.1050 | 0.1397 | 0.1387 | 0.0973 | 0.020 | 130 | 7 | 18 | 8 |
| hindsight: K=5 | 0.1065 | 0.1418 | 0.1408 | 0.0988 | 0.020 | 144 | 10 | 17 | 7 |
| hindsight: K=10 | 0.1095 | 0.1455 | 0.1447 | 0.1017 | 0.031 | 151 | 14 | 10 | 0 |

"...and wrong" counts confident misses: `p < 0.05` and the thing changed, or
`p > 0.95` and it didn't.

On the whole copy (to 2026-10-18 21:58, 2978 scored looks), the result is the
same. Overall Brier goes from 0.1171 to 0.1036. Short-fit Brier goes from 0.2647
to 0.1386. ECE goes from 0.037 to 0.020. Confident misses go from 45 to 7.

## What it means

* **The short-fit error is roughly halved:** 0.267 to 0.141. Overall, self goes
  from 0.1192 to 0.1056. That is a little worse than Hedge (0.1045). The dish
  is still 0.0035 better. The Petri lab estimated that removing the reset
  entirely would give about 0.105. The shrinkage reaches that level and keeps
  rung 5's re-learning.
* **Calibration improves:** ECE goes from 0.038 to 0.021, which is better than
  the dish's 0.026.
* **The number of near-0/1 predictions on short fits rises** (123 to 133).
  These are now mostly right. Confident misses fall from 44 to 7. Things that
  change on every look (`field:vitals`, `i`, `test:re-149`) are now predicted
  near 1 for good reason. Before, they could swing to `p ~ 0` on three quiet
  looks.
* **lam at the grid edge after a shift drops from 98 looks to 8.** All 8 are one
  chat (`chat:in:roomata-estorsky`). Its long history says it almost never
  changes, and it is looked at 30 minutes apart. Its best rate sits below
  1e-5/s, the grid's floor, where `p` is about 0.018. That is the grid's limit,
  not a three-look swing (1 of the 8 was a miss). The other 10 looks at the
  edge on short fits are young things that never shifted. #235 leaves those
  unchanged.
* **Cost:** fits over 10 or more looks get 0.0009 worse (0.0970 to 0.0979),
  because the prior lingers. A smaller K wins in hindsight on every column by a
  hair. K=3 stays, because it was the pre-registered choice and the
  differences are within 0.0006.
* **Patience can't be scored offline.** The looks were timed by the old
  predictor. A post-shift `p` that no longer collapses to 0 should stop
  patience from waiting the full 1800 s after a "went still" shift. This replay
  can't show that counterfactual.
* **Found while reading:** the fit window starts *after* the shift's timestamp.
  So the 3 to 5 looks that showed the shift (the ones rung 5's tempo re-learns
  from) are not in the post-shift rate fit. They are in its prior instead.
  #235 doesn't change that.

## Reproduce

```
cp ~/projects/membrane/.membrane/self/record.jsonl experiments/lam_shrink/record.copy.jsonl
python experiments/lam_shrink/run.py --until 1792373699   # the Petri lab's window
python experiments/lam_shrink/run.py                      # the whole copy
python -m pytest -q tests/test_lam_shrink_235.py
```
