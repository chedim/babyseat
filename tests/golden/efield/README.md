# Golden fixtures for the entity field (membrane/efield.py)

Recorded input plus the SAVED OUTPUT of the Python field. A port must reproduce
them within the tolerances below. Regenerate (knowingly) with
`python scripts/golden_efield.py`; `tests/test_golden_efield.py` fails when the
Python field drifts from them. Nothing here uses a model, the network or the wall
clock: vectors are `sha256(key) -> numpy default_rng -> standard_normal(64)`
(unit), every `ts`/`now`/`seq` is injected (`t0` = 1.8e9).

## How to consume

Every file has `inputs`: the event log in apply order. Each event carries
`seq`, `ts`, `stream`, `role`, `mentions` (`[eid, sentence_index, surface]`) and
`sentences` with `vec_f16`: the sentence vector AS THE FIELD STORES IT (float16
values widened to float64). The port feeds `unit(vec_f16)` as `x` and the term
seed is `seeded("seed|<surface>")` stored at f16 -- the seeds are NOT in the
files, only their effect (`seed` in each state snapshot, which is the f16 seed).
Apply the events one at a time in seq order and compare each recorded step.
`pure_functions.json` is input/output pairs for the pure functions.

## What each fixture pins

- `single_entity.json`: one file entity over 14 mentions (roles user/model/
  reasoning, a topic shift): `state` (f16 stored precision), `mean`, `rsum` (f32),
  `n`, `coherence`, `net_align`/`accr`/`last_accrue_ts` (ACCRETE=0.15 against the
  origin's running `pair` baseline), `echo`/`n_by_role`, episodes (EP_RUN=5),
  `last_x`, `recent`, `last_pos`; plus the origin stats (`bg`, `pair`, `boiler`)
  after every step, and a word entity that stays in the nursery.
- `fit_message.json`: a message vector fitted by NNLS onto the states of the
  mentioned live entities: `A_states` (before), `x_msg`, `weights`, `fit`,
  `residual`, `residual_norm`, the event's `surprise` and `coords` (weights
  normalised, rounded to 3 decimals, >= 0.02, top 8). Includes a pure-news
  message (surprise > 1 is correct: the fit can overshoot).
- `nursery_boilerplate.json`: a word waits in the nursery (`pending`,
  `distinct`) until K_MAT=3 DISTINCT sentences, then materialises from its seed
  replaying the buffered mentions in (seq, sidx) order; a repeated sentence adds
  a pending row but no distinct; the dust cap (a 3-letter word gets no state);
  boilerplate: the same normalised sentence heard > BOILER_MIN=8 times moves
  nothing and the event's `surprise` is null.
- `mass_metabolism.json`: `mass_eff` and `echo_now` at several `now`
  (including `now` before the last touch: dt clamps to 0) with
  MASS_METABOLISM False (mass == accr) and True (14-day half-life, conflict
  damp 1/(1+0.5*len), fallbacks last_accrue_ts -> last_ts -> no decay), for a
  state and for backend doc dicts.
- `rebuild.json`: `incremental` states equal `rebuilt` states after
  `rebuild()` (events re-marked and re-applied in seq order), exactly (diff 0.0),
  and the origin stats too. A port's rebuild must do the same.
- `pure_functions.json`: `unit` (zero vector stays zero: the 1e-12 term; a packed
  f16 string input), `at_stored`, `f32`, `move_to`/`move_step`, `coherence_of`
  (n < 2 is 0.0), `fit_weights` (degenerate/duplicate/near-collinear/zero rows,
  zero or orthogonal x -> uniform weights), `_nnls` incl. `max_on`.

## Dtypes (why the tolerances)

efield.py computes in float64 but ROUNDS at storage: `state`, `seed`, episode
centroids and `last_x` are f16-quantised (`pack_f16`) after every update;
`mean`, `rsum`, `ep_acc` are f32-quantised (`f32()`); sentence vectors enter as
f16 then `unit()` in float64; scalars (`coherence`, `net_align`, `surprise`,
weights) are float64. So a port MUST replicate the quantisation points (f16
round-to-nearest-even for state; f32 for mean/rsum), or errors compound.

Recommended tolerances (port vs fixture):

| quantity | tolerance | why |
|---|---|---|
| inputs `vec_f16` | exact | f16 values are exactly representable |
| `state`, `seed`, episode centroids, `last_x` (f16-stored) | abs 1e-3 on every component, and bit-exact f16 expected | one f16 ulp near a unit-vector component is ~2^-11 (4.9e-4 at 0.5-1.0); a 1-ulp flip from a differently ordered float64 sum is legitimate, more is a bug |
| `mean`, `rsum`, `ep_acc` (f32-stored) | abs 1e-6 | f32 has ~6e-8 relative; sums of unit vectors stay O(10) |
| `x_msg`, NNLS `weights`, `fit`, `residual` (float64) | abs 1e-9 (1e-7 when A is near-collinear) | float64 end to end; lstsq summation order differs by LAPACK; the near-collinear case is ill-conditioned, compare its `fit` not its `weights` |
| scalars `coherence`, `net_align`, `surprise`, `mass_eff`, `echo_now` | abs 1e-9 (rel 1e-9) | float64; `coherence` is a difference of near-equal sums, so keep rsum f32-exact first |
| integers/strings (`n`, `accr`, `n_by_role`, `pending`, `distinct`, `recent`, `last_pos`, `tier`, `ep_k`) | exact | `accr` flips when align crosses 0.15; the fixtures keep margins, a flip means a real divergence |
| `coords` | exact entities/order, weights exact (rounded to 3 dp) | |

`tests/test_golden_efield.py` itself compares Python to the files at abs 1e-9.

## Non-determinism found

None in the recorded values. Wall-clock reads exist in efield.py (`first_ts` on
sentence rows, `pull_current`/`pull_things` default `now`, `EntityState.mass`
uses `time.time()` when metabolism is on) but none reach a recorded field; the
fixtures inject `ts` and call `mass_eff(st, now)` directly. Set iteration order
in `_apply` is sorted (`sorted(mentioned)`), so entity order is stable.
