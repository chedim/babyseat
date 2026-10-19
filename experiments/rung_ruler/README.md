# the rung ruler: is there structure to learn? (#161, 2026-10-19)

**Verdict: the prediction half-holds, and E alone is not the ruler.** Rung 7's
target (the other answering by the 2-minute reply check) has E ≈ 0: 0.045 bit,
CI [-0.10, +0.20], no causal structure (1 state) and no order skill. Rungs 1, 3,
4 and 5 have clear E > 0 (0.23-0.88 bit, every CI far above the shuffled
null). But **rung 2 passed with E ≈ 0 too** (0.03 bit, CI [-0.07, +0.13]), and
rung 6 passed on a near-constant series. Those rungs pay for knowing a *rate*,
not an *order*, and E can't see a rate. And at rung 7's sample size
(n = 99) E can't separate rung 7 from passed rungs 2-4 at all: windows of 99
looks from those rungs clear their own null only 17-24% of the time. E is one
input to a fair-rung ruler; the ruler itself is the **achievable skill against
the rung's own baseline** (proposal below).

## the question

In computational mechanics (Crutchfield) a process's **excess entropy** E =
I(past; future) is how much its past tells about its future, in bits; its
**statistical complexity** C_μ is the entropy of its ε-machine's causal states.
A fair or biased coin has E = C_μ = 0. The idea: a rung is fair only if its
target has structure to learn. Rungs 1-6 passed live and should show E > 0.
Rung 7 has not passed, and its target looked coin-like (`experiments/rung7_recent`),
so it should show E ≈ 0. If so, E/C_μ could be used to design rungs 8-10 and
to scale their bars.

## the series

All series come from read-only copies, never the live files:

    D=/some/scratch/dir          # or experiments/rung_ruler/data (git-ignored)
    cp <seat>/.membrane/self/record.jsonl $D/record.jsonl
    # optional, the operator's touches (exp/rung7-attention's loader):
    cp <seat>/.membrane/serve-msgs.json $D/ && mkdir -p $D/toybox && cp <seat>/.membrane/toybox/*.log.jsonl $D/toybox/
    git show origin/exp/rung7-attention:experiments/rung7_attention/attention.py > /some/other/dir/attention.py
    python experiments/rung_ruler/ruler.py $D/record.jsonl [--attention /some/other/dir/attention.py]

Each series is the thing that rung's test (in `membrane/self_loop.py`) scores, in
record order. "Ordinary" looks exclude the `audit` and `audit5` looks.

| rung | series | symbols |
|---|---|---|
| 1 · feel itself | `push` of every cycle the world pushed (the current's strength; `_rung1` scores the push vector, but only its magnitude is on the record) | above / below the median (binary); terciles as a check (`1t`) |
| 2 · first reach | ordinary looks at the thing rung 2 passed on (`field:vitals`) | changed 1 / unchanged 0, no answer dropped |
| 3 · tell things apart | every ordinary reach, any thing (`_rung3` reads the last 80) | changed / unchanged / no answer (ternary) |
| 4 · patience | the timed looks, any thing (`_rung4` reads the last 40) | found news 1 / not 0 |
| 5 · notice the world changed | ordinary looks at `i`, the thing with the most `shift` lines after rung 2's (rung 5's audit passed on `test:re-149`, whose ordinary looks are 121 no-answers then 1045 changes: nothing to measure) | changed / unchanged |
| 6 · meet an other | answered looks at `chat:in:operator`, the other rung 6 passed on; `6'` the current other, `chat:in:roomata-estorsky` | changed / unchanged |
| 7 · understand an other | the reply checks (`reply` looks) at the current other; also the first 77, the record `rung7_recent` replayed | answered by the check 1 / not 0 |
| touch | the operator's turns (chat turns + toy-poke bursts, `exp/rung7-attention`'s loader) | the turn touched an entity 1 / not 0 |

## the estimators

`ruler.py`, pinned by `tests/test_rung_ruler_161.py` (written first). The
tests check that a fair coin gives E ≈ 0 and h_μ = 1, that period-2 gives
E = 1 bit and h_μ = 0, and that the golden-mean process gives h_μ = 2/3 and
E = 0.252 with 2 causal states and C_μ = 0.918. CSSR finds 1 state for a coin,
2 for period-2 and 3 for period-3. A biased coin at n = 1250 stays 1 state
across 10 seeds.

- **H(L)**: the entropy of the overlapping length-L blocks, with the Grassberger
  (2003) bias correction (Miller-Madow and plug-in are available).
  h(L) = H(L) − H(L−1), h_μ = h(Lmax), E = Σ_{L≤Lmax} (h(L) − h_μ). At Lmax = 2,
  E is the lag-1 mutual information.
- **Lmax**: the largest L with K^L ≤ n/10, so the blocks are seen ~10 times
  each if equally likely; at most 10. Rung 7 (n = 99) gets Lmax = 3, and the first
  77 get Lmax = 2. A finite Lmax under-counts long-range E, so an E here is a
  lower bound on the order structure up to Lmax.
- **CI**: a circular block bootstrap (500 reps, block length max(4·Lmax, n^⅓)).
  The junctions between blocks cut the very dependence E measures, which biases
  the resampled E low (about −0.04 bit on the golden mean). So the bootstrap
  gives the spread, and the interval is re-centred on the estimate.
- **Null**: E on 500 shuffled orders (same symbols, order destroyed). E_null
  is ≈ 0 everywhere, as it should be; its 95th percentile is the detection bar.
- **ε-machine**: a small CSSR (Shalizi & Klinkner), chi-square homogeneity at
  α = 0.001, histories with fewer than 5 samples skipped, then split until
  transitions are deterministic. Its Lmax is shorter (K^L ≤ n/50) because one
  false split at the longest length cascades: a biased coin at n = 1249
  with Lmax 6 came out as 6 states. C_μ is the entropy of the causal states'
  occupancy over the data. States below 1% occupancy count as transient.
- **power at rung 7's n**: 200 random windows of 99 symbols from each long
  series. Each window is tested against its own shuffled null at Lmax 3. The
  column gives the share of windows whose E clears the null's 95th percentile.
- **order skill**: the Brier error of the running rate (an order-0 Jeffreys
  predictor) against the best order-k Jeffreys predictor (k ≤ CSSR's Lmax),
  both prequential, with no look-ahead. That is what the series' *order* can
  buy, in the rung tests' own units. k is chosen in-sample, which is
  optimistic.

## the table

From the live record's copy at 2026-10-19 01:03, which held 21,444 lines.

| rung | n | K | rate | Lmax | H(1) | h_μ | **E** [95% CI] | E_null (q95) | causal states | C_μ | n=99 windows clearing null | order skill (Brier, rate → best k) | passed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 push (median) | 14341 | 2 | 0.50 | 10 | 1.000 | 0.545 | **0.884** [0.823, 0.953] | -0.001 (0.038) | 21 | 3.33 | 76% | 0.250 → 0.122, **+0.51** | yes |
| 1t push (terciles) | 14341 | 3 | - | 6 | 1.585 | 0.689 | **1.381** [1.344, 1.422] | 0.000 (0.018) | 20 | 3.43 | 83% | - | yes |
| 2 field:vitals | 1249 | 2 | 0.71 | 6 | 0.875 | 0.855 | **0.028** [-0.074, 0.128] | -0.003 (0.062) | 2 | 0.88 | 21% | 0.209 → 0.205, +0.02 | yes |
| 3 every reach | 6280 | 3 | - | 5 | 1.280 | 1.094 | **0.347** [0.299, 0.400] | 0.002 (0.023) | 18 | 3.25 | 24% | - | yes |
| 4 timed looks | 4703 | 2 | 0.80 | 8 | 0.714 | 0.616 | **0.235** [0.144, 0.313] | 0.014 (0.057) | 13 | 2.89 | 17% | 0.158 → 0.141, +0.11 | yes |
| 5 `i` | 1188 | 2 | 0.71 | 6 | 0.865 | 0.688 | **0.359** [0.224, 0.531] | 0.004 (0.066) | 8 | 2.37 | 64% | 0.206 → 0.166, +0.19 | yes |
| 6 chat:in:operator | 73 | 2 | 0.05 | 2 | 0.325 | 0.256 | **0.069** [0.002, 0.164] | 0.032 (0.054) | 1 | 0 | - | 0.059 → 0.049, +0.17 | yes |
| 6' current other (all looks) | 175 | 2 | 0.43 | 4 | 0.996 | 0.903 | **0.139** [-0.090, 0.386] | 0.000 (0.136) | 1 | 0 | - | 0.253 → 0.240, +0.05 | (rung 6 passed on operator) |
| **7 reply checks** | 99 | 2 | 0.55 | 3 | 1.009 | 0.986 | **0.045** [-0.098, 0.195] | 0.007 (0.120) | 1 | 0 | - | 0.260 → 0.268, **−0.03** | **no** |
| 7, first 77 | 77 | 2 | 0.56 | 2 | 1.009 | 1.042 | **−0.033** [-0.083, 0.032] | 0.008 (0.050) | 1 | 0 | - | 0.262 → 0.268, −0.03 | no |
| touch (operator turns) | 377 | 2 | 0.35 | 5 | 0.936 | 0.820 | **0.280** [0.089, 0.476] | 0.005 (0.125) | 3 | 1.27 | 54% | 0.231 → 0.212, +0.08 | - |

The full output also has lag-1 mutual information against its null and the
windows' median E; run the script to get it. Rung 7's 99 checks, oldest first:
`100110101011011111000110111110101101000010011000111100101000101100101110101111101111111101000000000`.
The first 77 are the record `rung7_recent` replayed. The 22 since end in a run
of nine 0s overnight, and that run is all that lifts E from −0.03 to +0.05.

## verdict

- **Rung 7's target is coin-like, as predicted.** E is 0.045 bit, CI
  [−0.10, +0.20], E − E_null +0.04, lag-1 MI 0.001 (null q95 0.037), 1 causal
  state, and order buys nothing (Brier −0.03). On the first 77, E is −0.03.
- **But "passed ⇒ E > 0" is false.** Rung 2 passed on `field:vitals`, which
  has E 0.03 bit, CI straddling 0, and 0.02 order skill. At the moment it passed,
  its last 20 looks went 19 of 20 changed, against a seat-wide rate of 0.78.
  Self's running per-thing rate scored skill +0.28 over the seat-wide baseline
  and the in-sample thing rate +0.38. **Rung 2 pays for a marginal offset (this
  thing's rate ≠ the seat's), which E is blind to by design.** Rung 6 asks for
  the opposite of structure (the other must be *un*predictable, skill < 0.9).
  It passed on a 4-in-73 series whose E CI only just clears 0. Rungs 1, 3, 4
  and 5 do sit far above their nulls.
- **At n ≈ 100, E can't tell rung 7 from rungs 2-4.** Windows of 99 looks clear
  their own null 17% (rung 4), 21% (rung 2) and 24% (rung 3) of the time.
  Only rungs 1 and 5, whose E at that n is about 0.2-0.3 bit, clear it in most
  windows (64-83%). Rung 7's CI top of +0.20 rules out structure as strong as
  rung 1's or rung 5's. It does not rule out rung-2/3/4-sized order structure
  (≲ 0.05 bit at Lmax 3), which a 99-look record can't see.
- **E ≈ 0 does not make rung 7 unfair.** Rung 7's bar is relative to *habit*,
  and habit is worse than a coin on this person (Brier 0.31 vs 0.25,
  `rung7_recent`). A predictor that always says 0.5 clears +0.15 on the
  pairs rung 7 read (+0.17), and over all 77 checks (+0.19). The running rate
  gets +0.16 over all 77 checks but only +0.06 on those pairs. So rung 7 is
  passable by calibration, with no understanding needed. The model's −0.34 is its yes-lean,
  not a missing signal. "Fair" depends on the baseline as much as on the target.
- **C_μ adds little here.** Where E is real, C_μ is large (2.4-3.4 bits, 8-21
  states) and grows with symbol resolution (1 vs 1t) and with n. Where E ≈ 0 it
  is 0, except rung 2's 2 states, which barely differ. It agrees with E's
  sign and doesn't rank rungs any better.
- **Bonus, the operator's touches.** Whether a turn touches an entity has
  E 0.28 bit [0.09, 0.48]. The structure is long-range: lag-1 MI is 0.007
  under its null, so it is sessions and phases, not turn-to-turn. That is the
  same kind of "joint attention" structure `rung7_attention` found in what is
  touched.

## caveats

- **Self-caused structure.** Rungs 3 and 4 interleave many things in self's own
  choice order, so part of their E is self's chooser (curiosity, patience),
  not the world. Rung 1's push depends partly on where self is.
- **Non-stationarity counts as E.** Regime changes (rung 5's `i`, the overnight
  run in rung 7, sessions in the touch series) raise E at finite Lmax. That is
  structure a learner can use (rung 5 is *about* it), but it is not stationary
  ε-machine structure. The causal-state counts on the long series are partly
  this.
- **Rung 1 is under-symbolized.** The record keeps the push magnitude, not
  the vector rung 1 predicts. Median vs tercile changes E (0.88 vs 1.38), as
  expected for a continuous process. Compare E within a symbolization, not
  across.
- **The rung-5 and rung-6 series are stand-ins.** Rung 5 passed on a
  membrane-made audit, which has no natural series, and rung 6's other changed
  between passing and now.
- **Small-n.** Rung 7 (99) and rung 6 (73) are under the n where this E has
  power. The first 77 get Lmax 2, which is just lag-1 MI. No n near 100 can
  exclude E ≲ 0.05 bit.

## a fair-rung ruler (proposal)

E answers "is there order to learn?"; a rung's test asks "can a predictor beat
*this baseline* by *this margin*?". Tie the bar to the latter, and use E for
what it can say:

1. **The ceiling.** On a replay of the rung's target (a toy or a record), compute
   the best achievable Brier skill against the rung's actual baseline,
   `S* = 1 − B_opt / B_baseline`. `B_opt` is the prequential Brier of the
   ε-machine (or best order-k) predictor plus any covariate the rung gives self
   (dt for rung 4, time-since-spoke for rung 7). S* splits into a **rate part**
   (`B_baseline` vs the running rate) and an **order part** (rate → best order,
   the last column, which is ≈ (H(1) − h_μ)-driven, and E > 0 is its necessary
   condition).
2. **The bar.** Set it at about half the ceiling's lower CI bound,
   `bar = ½ · S*_lo`, and only ship a rung whose S*_lo > 0. With today's numbers,
   rung 7's ceiling against habit is ≈ +0.17-0.19, all of it calibration (a
   humble ~0.5), so its 0.15 bar is near the ceiling: fair but tight. Rung 1's
   order ceiling is +0.51 against a 0.5 bar on a different metric (gap/naive).
3. **The claim.** If a rung's name claims *understanding* (rung 7 "understand
   an other", rung 8's mirror), require the **order** part: E − E_null must clear
   the null's q95 on the replay, and the bar must be scored against a baseline
   that already knows the rate (the running rate, not habit). Otherwise the rung
   is passable by calibration alone, which is what rung 7 is today.
4. **The n.** Choose the test's window from the power curve. To detect
   E ≈ 0.1-0.2 bit in a binary series at Lmax 3 more than half the time takes
   well over 100 symbols. Rung 7's 20 pairs can grade calibration, not order.
