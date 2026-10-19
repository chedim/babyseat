# rung 7's recent rate: the replay (#246, 2026-10-18)

Would a model told the other's RECENT reply rate (the last 6 reply checks)
beat habit by rung 7's margin (skill >= +0.15)? Measured on a read-only copy
of the live seat's record, never the live file:

    cp <seat>/.membrane/self/record.jsonl experiments/rung7_recent/record.copy.jsonl   # git-ignored
    python experiments/rung7_recent/replay.py experiments/rung7_recent/record.copy.jsonl

Each predictor sees only the reply checks before the one it predicts. `habit`
is the p self recorded; `all-time` and `recent` are (k+1/2)/(n+1) over all /
the last 6 earlier checks; `switch` takes recent when it differs from
all-time by more than 0.15; `blend w` = w*recent + (1-w)*all-time; `gap`
splits the rate by "they spoke within 10 min before the push"; `last bit` is
P(answer | previous check's answer). Skill = (B_habit - B) / B_habit.

The record: 77 reply checks at the other, 43 answered, 20 paired with a model
answer. Outcomes (oldest first):
`10011010101101111100011011111010110100001001100011110010100010110010111010111`
-- lag-1 P(1|1) = 0.50, P(1|0) = 0.62: no run structure, close to a coin.

| predictor | all 77: Brier / skill | last 20 checks: Brier / skill | last 20 PAIRED (what rung 7 reads): Brier / skill |
|---|---|---|---|
| habit | 0.310 / 0 | 0.343 / 0 | 0.301 / 0 |
| coin 0.5 | 0.250 / +0.19 | 0.250 / +0.27 | 0.250 / **+0.17** |
| all-time | 0.262 / +0.16 | 0.251 / +0.27 | 0.282 / +0.06 |
| recent (6) | 0.278 / +0.10 | 0.248 / +0.28 | 0.295 / +0.02 |
| switch | 0.270 / +0.13 | 0.256 / +0.26 | 0.292 / +0.03 |
| blend w=0.5 | 0.266 / +0.14 | 0.246 / +0.28 | 0.284 / +0.06 |
| blend w=0.67 | 0.269 / +0.13 | 0.246 / +0.28 | 0.287 / +0.05 |
| gap | 0.267 / +0.14 | 0.252 / +0.27 | 0.276 / +0.08 |
| last bit | 0.268 / +0.13 | 0.247 / +0.28 | 0.281 / +0.06 |
| model (recorded p_llm) | -- | -- | 0.402 / **-0.34** |

## verdict

- **The recent rate is not the missing information on this record.** The
  person's reply by the 2-minute check is close to a fair coin with no runs;
  the recent window is a noisier estimate of the same rate. Over all 77
  checks it is worse than the all-time rate (+0.10 vs +0.16), over the last
  20 checks it is a hair better (0.248 vs 0.251), over the 20 checks rung 7
  actually read it is worse (+0.02 vs +0.06). The record does not show the
  "silent for hours" the issue saw: the last 20 checks went 11 of 20.
- **Habit IS beatable here, by being humble.** Habit is worse than a coin
  (0.30-0.34): it swings 0.09-0.69 on a coin-like signal. A model that says
  ~0.5 would pass on the window rung 7 read (+0.17); a rate echo would land
  +0.02 to +0.08 there (and +0.27 on the last 20 checks, where habit dipped).
- **The live -0.34 is the model's lean, not its input.** It answered
  0.58-1.0 to checks that went 8 of 20. Giving it the recent rate can only
  help if it pulls the model toward the data; the sentence "weigh recent
  more" would, read literally (`switch`), cost a little on this person.
- So: whether +0.15 is reachable depends on the model answering near the
  rates it is told -- not on which rate. Habit's one-bit memory is not hard
  to beat on this rhythm; an overconfident model is what loses.
