# Membrane generalizes — plan (2026-10-10)

## Why

Membrane stores and recalls specifics well, but no part of it abstracts.
Every generalization so far came from outside (the LLM or engine code):

- the seat re-derived the same curls instead of reusing commands;
- the voice could quote a lesson, never learn its rule;
- 2081 entities, zero ISA links, until the operator prompted adoption;
- a shared "how much" direction exists in mxbai (r≈0.95, transfers across
  battery/tank/wind/temperature) and nothing in membrane noticed it;
- the light id was guessed instead of known ("lights look like this").

The dreamer does not fill the gap: it **imagines instances** (n-gram
continuations steered by open goals, kept as quarantined branches, voted by
reality) and never produces a type. It also skips every cycle when the goal
board is empty — most of 2026-10-09.

## The shape (complementary learning systems)

Sleep does two jobs: **replay/extract** (regularities across many episodes
become schemas, categories, skills) and **dream** (free recombination).
Membrane has the second only. The plan adds the first and gives the dreamer
a second role:

| part | who | does |
|---|---|---|
| day | everyone | experience → field events (said, observed, read) |
| **consolidation** (new night phase) | membrane, no LLM | replay the day; induce candidate generalizations |
| **testing** (dreamer, new role) | membrane, no LLM | imagine each candidate's consequences; check them against held-out experience |
| curation | Argus | sees surviving candidates *with their evidence*; names, accepts, rejects |
| use | engine + Argus | accepted ones work: inheritance, axes for goals/forecasts, habits in loops, voice patterns |
| reality | the world | keeps voting: a generalization that stops predicting decays |

Both remain: the self-driven phases propose and test; the dreamer keeps its
goal-steered imagination when goals are open, and tests candidates when they
are not (so it never idles on an empty board).

## The inductions (each a candidate generator)

1. **Classes by geometry** — entities that *sit and move alike* (state
   proximity + co-movement + shared getter keys), not only shared name stems.
   Baseline to beat: today's name-stem proposals.
2. **Axes** — directions that recur when *different* entities' readings
   change (movement regressed on value change, kept only if it holds across
   ≥2 entities). Prerequisite: readings must move entities (see Data).
3. **Habits** — recurring `event → command → receipt` triples across
   situations (named commands only; the command library makes these
   countable).
4. **Voice patterns** — sentence shapes whose varying parts are entities of
   one class → templates with typed slots (fills from live values).

Each candidate carries: its members/evidence, what it predicts, and a score.

## The test (the dreamer as scientist)

For a candidate, generate predictions and score them on experience the
induction did not see (time-split: induce on older, test on newer):

- class → a member's behaviour (its next readings / deltas) is predicted by
  the class better than by the member alone;
- axis → the axis reads values of entities not used to find it (r, error);
- habit → in held-out situations like the trigger, the command's receipt
  is the predicted one;
- pattern → held-out sentences of that shape are reproduced by the template.

Survivors (beating a simple baseline by a margin) become proposals; the rest
are dropped and logged. The score is kept and updated by reality afterwards.

## The metric

**Held-out predictive gain of accepted generalizations** — per kind and
overall, tracked nightly. "Membrane generalizes better" = this number goes
up. Shown in /membrane/state and the field viewer.

## Data prerequisites (check first)

- **Readings must reach the field.** Getter results land in `script_data`;
  whether they become field events that move the entity depends on the
  getter. The light class getter stores one big blob per member. Needed:
  readings flow in as meaningful events per member (its own state only).
- **Time-split history** of events/deltas per entity (exists: deltas carry
  seq/ts/event).
- **Named commands with runs/receipts** (exists since the command library).

## Rollout (one induction at a time, shadow first)

0. Measure the data: do getter readings move entities today? (fix if not)
1. Consolidation skeleton + **classes by geometry**, compared against name
   proposals on held-out behaviour. Shadow: log proposals, don't show.
2. Dreamer test role (+ never idle on an empty board).
3. Proposals with evidence in the seat's context; Argus curates.
4. Axes. 5. Habits (feeds the "LLM out of the loops" plan). 6. Voice patterns.

Each step: tests red-first, offline replay on a copy of the seat's field on
the Mac before the phone, the metric reported to the operator before the
next step.

## Guardrails

- No hard-coded ontology: inductions find structure; Argus names it.
- No automatic adoption: proposals only; accepting is an explicit call.
- Phone budget: consolidation runs in the night duty cycle, bounded (RAM
  peak measured; the 2026-10-08 LMK kill is the cautionary tale).
- A generalization that stops predicting decays; nothing is permanent by
  assertion.
- Every proposal and verdict audited (closes part of the deferred audit gap).

## Open questions for the operator

- Shadow period length per step (a night? three?).
- Should the voice's patterns (6) jump the queue, since speech was the
  original pain?
