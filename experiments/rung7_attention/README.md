# rung 7 as joint attention: the replay (#161, 2026-10-19)

**Verdict.** Against popularity the bar is reachable, and too easily: a
3-turn recency cache beats it by +0.29 log-loss skill (CI +0.21..+0.35).
Against that recency cache nothing simple beats it: bigram gets -0.01 and
field neighbours +0.00, with both CIs straddling 0. The current self does
not pass either bar. Its own look trail scores -0.01 vs popularity, and
self looks at 22 entities while the operator touches 144. If rung 7 asks
"which entity next?", score it against **recency**, not popularity. Even
then, this record has not shown that the bar is reachable; see the caveats.

The question: rung 7 asks "will the other reply?". The answer is coin-like
on this person (`experiments/rung7_recent`), so we tried a joint-attention
question instead: "which entity will the operator touch next?", scored
against "whatever they touch most". This replay ran on read-only copies of
the live seat's record, never on the live files:

    D=experiments/rung7_attention/data                 # git-ignored (data/.gitignore)
    cp <seat>/.membrane/serve-msgs.json <seat>/.membrane/self/record.jsonl $D/
    mkdir -p $D/toybox && cp <seat>/.membrane/toybox/*.log.jsonl $D/toybox/
    # optional, for the field-neighbour predictor: a COPY of the field
    cp -r <seat>/.membrane/field.cblite /scratch/f/field.cblite
    MEMBRANE_CBL_DIR=vendor/cbl/macos python experiments/rung7_attention/field_vecs.py /scratch/f/field.cblite $D
    python experiments/rung7_attention/attention.py $D

The scoring code is pinned by `tests/test_rung7_attention_161.py`.

## the sequence

Where the operator's touches are recorded:

- **Chat turns** (`serve-msgs.json`). A turn is a user message headed
  `[user: Roomata Estorsky]` or `[user: Dima]`; system pokes, Claude and
  voice-lane preambles are not turns. The turn's touches are the entity
  specs (`spec/name/entity/eid/key/target`) of the entity-family tool calls
  the agent made while answering it (up to the session's next user
  message), plus known entity keys the operator typed in the text.
- **Toybox pokes** (`toybox/*.log.jsonl`, `by` = the person's name). Pokes
  closer than 2 minutes make one turn.
- `audit.jsonl` has no timestamps, so it cannot be ordered and was not used.
  Self's `record.jsonl` gives self's looks (`reach`), not the operator's
  touches.

**Granularity:** entity keys per turn, deduped within the turn. Ids fold to
one key: the `membrane::` namespace, the `Component:` class and the `m:` and
`models:` mirror prefixes are dropped, and `files:` becomes `f:`. The
channel is dropped (`chat:*`, `person:`, `voice:`, `seat:`, `e:`, `word:`,
`session:`): the operator speaking is not what they attend to. Commands
(`exec`) are not counted.

**The record.** 2026-10-15 12:35 to 2026-10-18 23:58, mostly one long
session. Of 376 operator turns (372 chat, 4 poke bursts), 131 touch at
least one entity. They make 336 touch events on **144 distinct entities**.

- **Concentration:** the top entity has 7% of the touches, the top 5 have
  27% and the top 10 have 42%. 110 entities are touched only once. Entropy
  is 6.24 bits, against 7.17 bits if touches were spread evenly over 144
  entities.
- **Top entities:** test:re-149 23, toy:die 18, self 17,
  knock:hello-from-roomata 17, toy:tower 17, knock:hello-from-memo-1449 12,
  w:self 10, w:prime 10.
- **43% of the events are first touches** (144 of 336). No predictor can name
  an entity it has never seen, so this caps every score.

## method

The replay is prequential with no lookahead. Every event in a turn is
predicted from the state before that turn. Each predictor names a
distribution q over candidate entities.

**The novel bucket.** A never-seen entity costs every predictor the same.
All predictors share one Witten-Bell "other" bucket, eps = D/(N+D), where N
is the number of events seen so far and D the number of distinct entities
among them:

- if the predictor named the entity, p(e) = (1-eps) q(e);
- otherwise p(e) = eps.

**Metrics.** Log-loss skill = 1 - LL/LL_baseline. hit@k counts an event
whose entity is in the predictor's top k; a novel entity is a miss.

**Tuning and the held-out tail.** Parameters were tuned on the first 70% of
the touch turns (91 turns) and scored on the held-out tail: 40 turns and
100 events, 36 of them novel. A 50/50 split is the sensitivity check.

**CIs and the null.**

- 95% CIs come from a paired bootstrap over whole turns (2000 resamples).
- The **shuffled-order null** permutes the turn order 50 times and re-scores
  each predictor against popularity on the same permutation.

The predictors:

- **(a) popularity:** cumulative counts. This is the baseline.
- **(b) recency:** counts decayed by a half-life in turns, mixed with
  popularity. Also "repeat last turn" mixed with popularity, the cheapest
  recency.
- **(c) bigram:** every entity of turn i-1 → every entity of turn i, with
  additive backoff to popularity.
- **(d) field neighbours:** softmax of the cosine between the last turn's
  entities and the seen entities, mixed with popularity. Positions are the
  `mean` of `entity_states` in a copy of the field. 267 of the 336 events
  have a position.
- **(e) self's look trail:** self's `reach` looks decayed by a half-life in
  seconds up to the turn's time, alone and mixed with popularity. Self only
  exists for the last 111 touch turns (286 events).

## results: against popularity (held-out tail, 100 events)

| predictor | params | LL (nats) | skill vs pop [95% CI] | hit@1 | hit@5 | Δhit@1 [CI] | Δhit@5 [CI] | whole-seq skill | shuffled-order null |
|---|---|---|---|---|---|---|---|---|---|
| popularity | | 3.247 | 0 | 0.00 | 0.14 | | | 0 | |
| **recency (decayed)** | half 3 turns, λ 0.8 | **2.315** | **+0.287 [+0.209, +0.351]** | **0.20** | **0.49** | [+0.135, +0.276] | [+0.225, +0.484] | +0.175 | -0.107 [-0.140, -0.069] |
| repeat last turn | λ 0.3 | 2.659 | +0.181 [+0.113, +0.250] | 0.18 | 0.40 | [+0.114, +0.263] | [+0.153, +0.378] | +0.127 | -0.047 [-0.075, -0.022] |
| field neighbours | T 0.02, λ 0.4 | 2.554 | +0.214 [+0.136, +0.291] | 0.17 | 0.41 | [+0.111, +0.242] | [+0.161, +0.388] | +0.133 | -0.063 [-0.097, -0.033] |
| bigram | β 10 | 2.905 | +0.105 [+0.031, +0.175] | 0.13 | 0.35 | [+0.071, +0.206] | [+0.087, +0.337] | +0.062 | -0.038 [-0.067, -0.006] |

**On a 50/50 split** (170 tail events) the ranking holds:

- recency +0.205 [+0.122, +0.291];
- field neighbours +0.168;
- repeat +0.151;
- bigram +0.076.

**The shuffled null is negative for every predictor.** The gain comes from
the order of the touches, which is the point of the question.

**Recency's gain is conversational.** These numbers are over the whole
sequence with the tuned settings:

| gap since the previous touch turn | turns | skill |
|---|---|---|
| under 10 min | 86 | +0.22 |
| 10 min to 3 h | 41 | +0.10 |
| over 3 h | 3 | |

## results: against RECENCY (same tail)

| predictor | w on recency | skill vs recency [95% CI] | hit@1 | hit@5 | Δhit@1 [CI] |
|---|---|---|---|---|---|
| recency | 1 | 0 | 0.20 | 0.49 | |
| recency + bigram | 0.85 | -0.011 [-0.022, +0.002] | 0.20 | 0.49 | [0, 0] |
| recency + field neighbours | 0.7 | +0.001 [-0.016, +0.020] | 0.22 | 0.51 | [0.000, +0.053] |

Once recency is in, neither the transition structure nor the field geometry
adds anything measurable. The field-neighbour gain over popularity was
mostly recency: it predicts from the last turn's entities.

## self's look trail: is the current self already there?

No.

| what self's trail gives | hit@1 | hit@5 | skill vs popularity |
|---|---|---|---|
| popularity, on the same 286 events | 0.05 | 0.21 | 0 |
| trail alone, half-life 2 min | 0.06 | 0.12 | |
| trail alone, half-life 10 min | 0.07 | 0.12 | |
| trail alone, half-life 1 h | 0.07 | 0.09 | |
| trail + popularity, best of 12 settings (optimistic) | 0.06 | 0.18 | -0.010 [-0.032, +0.008] |

The best of the 12 settings was picked on the same events it was scored on,
so even its -0.010 is optimistic.

**Why the trail does not help.** Self looks at 22 distinct entities in 5763
looks. Most of them are field:vitals, test:re-149, i and w:body. The
operator touched 144 entities. Only 103 of the 286 operator events land on
an entity self ever looked at.

**Lead and follow.** Within 10 minutes of a turn, self's looks include a
touched entity:

- in the 10 minutes before the turn (self leads): 38 of 106 turns;
- in the 10 minutes after it (self follows): 43 of 106 turns.

That is a weak follow and no lead. The current self's attention neither
anticipates the operator's nor clears the bar.

## recommendation

- **Question:** "which entity will the operator touch next?" A turn's touches
  are the entities that operator turn reached, deduped, with the channel
  dropped.
- **Scoring:** log-loss with the shared novel bucket (eps = D/(N+D)), plus
  hit@5 as the readable number.
- **Baseline: a decayed recency cache,** half-life about 3 turns, mixed 0.8
  with popularity. Popularity is too weak: on this record a three-line
  cache beats it by +0.29. A rung that recency passes would measure memory
  of the last few turns, not understanding of the other.
- **Bar:** skill vs recency ≥ +0.05, with the bootstrap lower bound > 0,
  over the last 40 touch turns. The +0.05 is a guess: no simple predictor
  got there on this record. The best stacked predictor's CI half-width is
  about 0.02, so +0.05 is clearly beyond noise. Whether any predictor,
  self or a model, can reach it is open. That is the honest state: against
  recency the question is well-posed (the null is clean and the baseline is
  strong), but this record has not shown the bar is reachable.
- **What self needs to try it live:** a feed of the operator's per-turn
  touches. Today self only sees chat:in, its own looks and toy pokes. The
  agent's tool calls during an operator turn are not attributed to the
  person anywhere self reads.

## caveats

- **Small n.** 131 touch turns, 336 events, a 40-turn and 100-event tail,
  3.5 days, one person, essentially one session. The CIs are wide, and
  "nothing beats recency" is a statement about this record.
- **The touches are mostly the agent's.** What an operator turn "touched" is
  mostly what the agent's tool calls reached while answering it. The strict
  sequence of what the operator named or poked themselves has only 8 turns
  and 14 events: too small to score, so it is reported but not tested. If
  rung 7 should be about the person and not their agent, this record cannot
  answer it yet.
- **Novel entities cap everything.** 43% of all events and 36% of the tail
  are first touches. The best possible hit@k on the tail is 0.64.
- **The field geometry is end-of-record.** The positions come from a static
  snapshot that has seen the whole period, a mild lookahead that favours
  (d). It still adds nothing over recency.
- **Self is young.** Self only existed for 111 of the 131 touch turns, and
  it was looking at a self-chosen handful of entities (its ladder,
  field:vitals, toys) that it was never asked to align with the operator.
  A trail that does not predict is a statement about today's self, not
  about what self could learn.
- **Ids are normalized heuristically.** Dropping the `m:` and `models:`
  mirrors is a judgement call: it merges the mirror view of an entity into
  the entity. Text matching only recognises keys with `:` and at least 5
  characters.
