# lam window: how many looks rung 4's rate fit should remember (#293)

**Verdict: 30 stays, now measured.** Over self's own record, a 30-look window
predicts best overall and is the best-calibrated. 20 and 60 are close (within
0.003 Brier), 120 a little worse, and 10 clearly worse. 30 was picked by hand
in #178; this is the measurement behind it.

**Question.** Rung 4 fits each thing's rate of change by maximum likelihood
over its last `LAM_LOOKS` answered looks. A shorter window follows a changing
world faster but chases flukes; a longer one is steadier but slower. Which
window predicts self's world best?

## Harness

`run.py` replays a read-only copy of the record (`record.copy.jsonl`,
git-ignored, never committed) through `experiments/lam_shrink/run.py`'s
harness: each thing's answered looks outside audits, in time order (predict,
then learn), the first 30% of each thing's looks as warm-up, the timed and
one-bit logic, and the **recorded** rung-5 shifts. The rate fit is
`self_loop._lam` itself with `LAM_LOOKS` set to each window, made incremental
(`FastLam`); `tests/test_lam_window_293.py` checks it equals `_lam` at every
look of a record with several things, audits and shifts, at every window
swept.

**The replay is self.** Self's recorded `p` came from a different fit before
#235 reached the seat, so `--since` scores only the looks after it (the
replay still learns from every look). On those, the 30-look window
reproduces self's recorded Brier and ECE to four decimals.

## Results (Brier per look, lower is better; ECE in brackets)

Copy of 2026-10-20 00:49 UTC, 34,886 record lines, 23 things.

| window | after #235 merged (6,063 looks) | from 10-19 00:00 EDT (5,702) | from 10-19 04:53 EDT (4,501) |
|---|---|---|---|
| self's recorded `p` | 0.1054 (0.015) | 0.1021 (0.014) | 0.0993 (0.013) |
| 10 | 0.1198 (0.048) | 0.1160 (0.046) | 0.1153 (0.049) |
| 20 | 0.1082 (0.017) | 0.1050 (0.015) | 0.1024 (0.013) |
| **30 (today)** | **0.1054 (0.015)** | **0.1021 (0.014)** | **0.0993 (0.013)** |
| 60 | 0.1062 (0.017) | 0.1036 (0.015) | 0.1001 (0.016) |
| 120 | 0.1070 (0.022) | 0.1046 (0.021) | 0.1004 (0.019) |

The cuts are `--since 1792375832` (#235's merge), `1792382400` and
`1792400000`. On the whole copy, which mixes in the pre-#235 fit, the order is
the same: 30 at 0.0992, then 60 (0.1004), 20 (0.1015), 120 (0.1031), 10 (0.1125).

## What it means

* **30 sits in a flat optimum.** 20–60 are within 0.003 of it. Only a short
  memory hurts: at 10 looks the fit chases flukes, and its calibration error
  triples.
* **Per thing, the best window varies, but not usefully.** Things that change
  in sessions (tower blocks, the chat) sometimes do better at 10 or at 120;
  steady ones are flat across 20–120. The gains are small, and most come from
  things with 30–40 scored looks, too few to choose a window on. A per-thing
  window chosen from each thing's own past (tried in the chat that led to
  #293) scored the same as 30 overall.
* **The runs of misses after a surprise aren't a memory problem.** No window
  removes them: a single rate per thing can't foresee a person sitting down
  to play. Predicting those needs context (who is here, what else moved),
  which is rung 8 territory, not rung 4's.

## Reproduce

```
cp ~/projects/membrane/.membrane/self/record.jsonl experiments/lam_window/record.copy.jsonl
python experiments/lam_window/run.py --since 1792375832
python -m pytest -q tests/test_lam_window_293.py
```
