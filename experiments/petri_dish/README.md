# petri dish: competing predictors vs self's one predictor (#161, lab)

**Question.** Self predicts, before each look at a thing, the chance it has
changed (`p` in `.membrane/self/record.jsonl`). Does a Petri-style *dish* of
competing predictor variants (population, application in proportion to copy
count, reproduction of what predicts well) beat that one predictor? And is it
anything more than a standard online-learning mixture?

**Verdict.** The dish beats self's live predictor by **0.017 Brier** (0.1021 vs
0.1192, a 14% cut; thing-bootstrap 95% CI [-0.031, -0.003]). It does **not**
clearly beat the static equal-weight mixture of the same variants (-0.0022, CI
[-0.0067, +0.0011]) or Hedge (-0.0024 vs eta=2, CI [-0.014, +0.007]). Almost
all of the gain comes from the **pool of variants** and from **not re-fitting
the rate on a handful of looks after a shift**. Selection adds little. The
dish is Hedge with sign-based updates and a floor, nothing beyond it.

## Data

A read-only copy of the live record (2026-10-16 10:57 to 2026-10-18 21:34, one
seat, `record.copy.jsonl`, git-ignored, never committed). It holds 4137
answered looks at 22 things (audit and audit5 looks and `none` answers left
out). The first 30% of each thing's looks are warm-up: every method learns from
them, none is scored on them. That leaves **2887 scored looks**. Nine things
have fewer than 20 looks (flagged `few` in `results.json`): every toy, `w:drift`,
`w:slow`, `cradle:for-self` and `probe:script-api`.

Kinds are hindsight labels for reporting only. `chat:in` and `toys` go by name.
The rest go by their overall change rate: moving >= 0.6, still <= 0.2,
otherwise intermittent. The moving class (2583 looks) dominates the pooled score.
Two of its things (`test:re-149`, `w:body`, change rate 1.0) score about 0 under
every method. The **macro** column (the mean of the per-thing Brier) weighs
every thing equally.

## Methods (all time-ordered: predict, then learn; no lookahead)

* **self_A**: self's recorded `p`, the honest live baseline.
* **self_B**: a faithful offline re-implementation of `self_loop._expect`.
  On a timed look (and on a reply check once timed looks exist), it uses
  `1 - exp(-lam*dt)` with lam the grid MLE over the last `LAM_LOOKS=30`
  answered looks since the thing's last recorded `shift`. Otherwise it uses the
  one bit: Jeffreys-smoothed `P(changed | previous answer)`, falling back to the
  smoothed marginal. Shifts reset tempo exactly as `_shift_check` does.
  **Sanity:** the mean |A - B| is 0.025 and 88% of looks fall within 0.01. The
  Brier is identical to 4 decimals (0.1192 / 0.1193).
* **Variants (funclets)**, `dish.make_variants()`:
  * `onebit`: self's one bit, without shift resets.
  * `base`: the smoothed base rate.
  * `rate_w30`: self's rate fit, fed continuously, no shift reset.
  * `rate_h10`, `rate_h50`: a Poisson rate MLE over every look, weighted
    `0.5**(age/h)`.
  * `ema_h3`, `ema_h10`, `ema_h30`: an EMA of the change frequency.
  * `last3`, `last5`: the smoothed share of changes among the last k looks.
  * The rate variants use self's recorded `dt`.
* **dish** (`dish.Dish`, one dish per thing). The constants are pinned and
  pre-registered before the run:
  * Every variant starts with `INIT=10` copies.
  * `mix`: the prediction is the copy-weighted mean.
  * After the outcome, each variant's Brier is compared with the mean's Brier.
    A variant better by more than `TIE=1e-9` returns `this` and multiplies:
    `c += max(1, round(GROW*c))`. A worse one is consumed:
    `c -= max(1, round(GROW*c))`. A tie keeps its copies. `GROW=0.25`.
  * `FLOOR=1`: nothing goes extinct. `CAP=1000`: when the total goes over,
    every count is scaled by `CAP/total`, floored at 1, and the largest is
    trimmed until the total fits.
  * `sample`: the same copy dynamics, but the output is ONE variant drawn in
    proportion to its copies (seeded, `SEED=161`). This isolates sampling vs
    mixture.
  * `fire` (strict Petri): one variant is drawn and only that one is judged
    against the hidden mean (`this`+`*` or consumed).
* **Controls.**
  * (a) The best single variant in hindsight, chosen globally and also per
    thing. This is an upper bound no non-adaptive method can know.
  * (b) `static_mix`: the equal-weight mean of the same variants.
  * (c) `Hedge`: weights proportional to `exp(-eta * cumulative Brier)`.
    `eta=2` was pre-registered; `eta` in {0.5..32} is a hindsight sweep.
  * The `GROW` sweep {0.1, 0.25, 0.5, 1} is also hindsight. Only `GROW=0.25`
    and `eta=2` are the honest comparisons.

## Results (Brier per look; lower is better)

| method | pooled (2887) | macro (22 things) | chat:in (2 / 139) | intermittent (3 / 32) | moving (6 / 2583) | still (4 / 118) | toys (7 / 15) |
|---|---|---|---|---|---|---|---|
| self_A (live `p`) | 0.1192 | 0.1324 | 0.1893 | 0.3493 | 0.1170 | 0.0294 | **0.0595** |
| self_B (re-implemented) | 0.1193 | 0.1324 | 0.1893 | 0.3493 | 0.1171 | 0.0294 | 0.0595 |
| static_mix (no selection) | 0.1043 | 0.1192 | 0.1754 | 0.2326 | 0.1024 | 0.0277 | 0.0897 |
| hedge eta=2 (pre-registered) | 0.1045 | 0.1161 | 0.1765 | 0.2115 | 0.1029 | 0.0272 | 0.0844 |
| hedge eta=0.5 (best pooled, hindsight) | 0.1040 | 0.1180 | 0.1729 | 0.2252 | 0.1023 | 0.0272 | 0.0883 |
| **dish mix GROW=0.25** | **0.1021** | **0.1147** | 0.1753 | 0.2033 | 0.1004 | 0.0277 | 0.0822 |
| dish sample GROW=0.25 | 0.1057 | 0.1202 | 0.1765 | 0.2104 | 0.1042 | 0.0306 | 0.0923 |
| dish fire GROW=0.25 (strict Petri) | 0.1082 | 0.1198 | 0.1807 | 0.2406 | 0.1063 | 0.0279 | 0.1126 |
| dish mix GROW=0.1 (best of sweep, hindsight) | 0.0992 | 0.1161 | 0.1754 | 0.2153 | 0.0970 | 0.0269 | 0.0863 |
| best single, global (hindsight: rate_h10) | 0.1043 | 0.1121 | 0.2119 | 0.1946 | 0.1011 | 0.0290 | 0.0585 |
| best single, per thing (hindsight) | 0.1000 | 0.0879 | 0.1689 | 0.1771 | 0.0991 | 0.0243 | 0.0384 |
| single: rate_w30 (self's fit, no shift reset) | 0.1085 | 0.1129 | 0.2165 | 0.1933 | 0.1056 | 0.0294 | 0.0585 |
| single: onebit | 0.1156 | 0.1252 | 0.1815 | 0.2586 | 0.1146 | 0.0256 | 0.0869 |

The column headers give things / scored looks.

**Paired vs the pre-registered dish (dish mix GROW=0.25 minus X).** Each row
gives the look-level mean and a thing-block bootstrap 95% CI (2000 resamples).

| X | mean | 95% CI |
|---|---|---|
| self_A | -0.0171 | [-0.0313, -0.0030] |
| static_mix | -0.0022 | [-0.0067, +0.0011] |
| hedge eta=2 | -0.0024 | [-0.0141, +0.0071] |
| dish sample | -0.0037 | [-0.0067, -0.0005] |
| dish fire | -0.0061 | [-0.0098, -0.0018] |
| best single global (hindsight) | -0.0022 | [-0.0118, +0.0055] |
| best single per thing (hindsight) | +0.0021 | [-0.0037, +0.0087] |

**Calibration** (ECE over 10 bins, pooled):

| method | ECE |
|---|---|
| self_A | 0.038 |
| self_B | 0.038 |
| static_mix | 0.028 |
| hedge eta=2 | 0.032 |
| dish mix | **0.026** |
| dish sample | 0.036 |

Self's worst bin is `p` < 0.1: 217 looks at a mean `p` of 0.028, and 20.7% of
them changed. The dish mix has 167 looks in that bin, and 4.2% of them changed.
The full reliability tables are in `results.json`.

**Where self loses.** This is the most actionable finding.

| looks by self's rate fit | looks | self_A | rate_w30 on the same looks |
|---|---|---|---|
| one bit (no rate) | 62 | 0.2315 | 0.1847 |
| rate fitted over < 10 looks | 328 | **0.2672** | 0.1518 |
| rate fitted over >= 10 looks | 2497 | 0.0970 | 0.1010 |

After a rung-5 `shift`, self re-fits lam over the 3 to 5 looks that showed the
shift. On those short-fit looks, lam sits at the grid's edge (1e-5 or 1.0) about
37% of the time, and `p` falls under 0.05 or over 0.95 about 42% of the time.
The thing then keeps changing, or doesn't. These 11% of looks carry most of
self's excess error. With 10 or more looks in the fit, self is as good as any
variant. If those looks are swapped for the un-reset fit, self comes to
about 0.105 pooled. The dish's remaining edge over that repaired self is about
0.003.

**Dish populations at the end** (`dish_copies_end` in `results.json`): the dish
goes nearly winner-take-all on the busy things:
* `field:vitals`: rate_h10 holds 989 of 1000 copies.
* `knock`, `w:fast`, `w:rung`: ema_h3 holds 991 of 1000.
* `test:re-149`, `w:body`: the three rate variants hold 330 each.

Short-lived things stay close to the starting mix.

## What it means for self

* **Does the dish beat self's predictor? Yes, by 0.017 Brier**, and the CI
  excludes zero. But it is **not the selection that does it.** The static
  equal-weight mix already gets 0.015 of the 0.017. The dish is
  indistinguishable from Hedge here, and the hindsight-best single variant is
  within noise. Petri's rule (copies x on a win, consumed on a loss, floor 1)
  is Hedge with sign-based updates and a floor of exploration. At this data
  size it is neither better nor worse.
* **Mixture beats sampling** (0.0037, CI excludes 0), and strict-Petri `fire`
  is worse again (0.0061). That is Jensen: Brier is convex, so a drawn
  variant's expected loss is at least the mean's. Petri's literal "apply one
  funclet in proportion to copies" costs accuracy. Its *expectation* is what
  predicts well.
* **The toy calibration problem is not fixed, and gets worse.** There are 15
  scored looks across 7 toys, all flagged. Self's mean `p` on them is 0.15, the
  dish's is 0.25, and nothing changed. A pool of slow learners stays near 0.5
  longer than self's one bit does. Few-look things need a better prior (for
  example, a seat-wide base rate per kind), not more competitors.
* **Patience and attention: can't be measured offline here.** Self's looks
  were timed by its own predictor, so every method is scored on the dt that
  self chose. A dish driving the patience would change when looks happen. This
  experiment cannot score that counterfactual. What it does show is that the
  overconfident post-shift lam (`p` near 0, which tells patience to wait the
  full 1800 s) is the likely source of missed changes. The cheap repair is a
  minimum fit size, or a shrinkage prior on lam after a shift. That is one
  line in `_lam`, not a dish.
* **Does it argue for building Petri's VM? Not on this evidence.** The
  predictive value is in having several predictors and averaging them, which
  is a 30-line Hedge or static mix. The population and consumption semantics
  add nothing measurable, and Petri's sampling semantics subtract.

## Caveats

* One seat over 2.5 days, 22 things. The pooled score is dominated by
  `field:vitals` and `i`, which have 1333 of the 2887 scored looks.
* Kinds are hindsight labels.
* Self's 0.017 gap is driven mostly by the post-shift re-fit. Self's shift
  detector (rung 5) is a feature the variants lack, and here it hurts.
* The variants are fed self's recorded `dt` (time since self's previous
  answered look, which includes audit5 looks). Self's predictor used the same
  `dt`.
* The `GROW` and `eta` sweeps are hindsight. GROW=0.1 reaches 0.0992 pooled,
  but it was picked after the fact.
* `sample` and `fire` use one seed (161). Their gap to `mix` is structural
  (Jensen), not luck.
* The 30% warm-up is the same for every method. Self's live `p` also benefited
  from looks before the warm-up cut, like every other method.

## Reproduce

```
cp ~/projects/membrane/.membrane/self/record.jsonl experiments/petri_dish/record.copy.jsonl
python experiments/petri_dish/run.py            # writes results.json, prints the table
python -m pytest -q tests/test_petri_dish.py    # the dish's mechanics
```
