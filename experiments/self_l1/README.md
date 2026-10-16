# layer 1 in the lab: can a self find its body? (#161, 2026-10-15)

The contingency ledger (`contingency.py`) asks of every entity: how much more
often does it change in the window after *my* act than on its own? Entities that
answer nearly always, at a steady short delay, are **body**; ones that answer
above chance but unreliably or late are **others**; the rest is **world**.

    python sandbox.py                 # 10 toy worlds: recovery, the null, stability, the learning curve
    python replay.py <store.cblite>   # rung 2: the same ledger over a real seat, read-only

## the sandbox (`sandbox.py`): a world with a planted answer

`self` babbles 5 kinds of act at random (~1 per 20 steps). Planted truth:
- **4 body parts**: one answers every act, three answer one act kind each (p 0.9-0.95, delay 0-1), and each also twitches on its own now and then;
- **2 others**: answer any act about half the time, 3-10 steps late, and act on their own too;
- **24 world entities**: spontaneous churn from rare (0.001/step) to frantic (0.3/step).

Window 12 steps (wide enough to catch the slow others), 8000 steps (~400 acts), 10 seeds:

| | body found / claimed correctly | others found / claimed correctly | body found from shuffled acts (null) | same body in both halves |
|---|---|---|---|---|
| steadiness = std of delays | 0.70 / 1.00 | 1.00 / 0.63 | 0 | 0.60 |
| **steadiness = MAD of delays** | **1.00 / 1.00** | **1.00 / 0.97** | **0** | **0.97** |

Learning curve (body recall vs acts seen): **0.00** at ~50 acts (ten of each
kind), 0.62 at ~100, 0.97 at ~200, **1.00** at ~400. The ledger will not name a
body part from a handful of acts -- see `MIN_KIND_ACTS` in rung 2 -- so the
curve starts later than it used to: an act kind has to be exercised before it
can be seen to answer.

Read: the std version lost a body part whenever it missed one act and its
answer to the *next* act was counted as a long delay: one stray in 70 pushed
its spread over the line, and more data didn't help. The median absolute
deviation is robust to that, and now more experience gives a better body
map, which is what layer 1 needs. The null never finds a body. The one
remaining slip is a single frantic world entity called "other" in 1 of 10
worlds.

**Caveat:** this world was designed by us, so passing it only shows that the
method can work. The next rung replays a real seat's history (its acts in the
audit/predict ledgers, the field's change pulses) and asks whether the body
map that emerges makes sense and stays stable.

# rung 2: the same ledger over a real seat (#161, 2026-10-15)

`replay.py` reads a live seat's own history instead of a toy: its event log
(who spoke, which tool ran, when) and the field's change pulses
(`entity_deltas`), 10.2 h of a working seat -- **3682 events, 28166 pulses**.
It takes a read-only copy of the store first (`snapshot()`, SQLite's own
backup: consistent while the seat keeps writing, 0.9 s for 360 MB) and never
opens the live one.

    python replay.py ~/.membrane/field.cblite     # reads a snapshot of it

## what the replay found

**The pulses are mostly the seat's own words.**

| who owns the pulse | pulses | share |
|---|---|---|
| the seat's own words (its reasoning + its replies) | 21361 | 76% |
| the operator | 2609 | 9% |
| the agent's tool calls (the act's own footprint) | 2142 | 8% |
| the seat's machinery (ticks, consequences, watch verdicts) | 1848 | 7% |
| notes | 206 | 1% |

And 14779 of 28166 pulses (**52%**) are literally *this entity is named in that
event*. The field's change pulse is a **mention** stream, not a motor stream:
an entity moves when a sentence names it, and the seat's own sentences name
far more than its acts do.

Replayed naively -- every pulse counts -- the ledger calls 6 things body at a
20 s window, and every one is the act's own echo: `tool:shell` (the tool entity
of my own `exec`), `s:?entity_read` after `entity_read`, `s:?entity_edit` after
`entity_edit`, `s:?entity_query` after `entity_query`, `s:?entities_related`,
and `seat:self`. It is reading back the act's signature, not finding a body.

So the replay counts an answer only when it is owned by an event that is
**neither the act itself nor the seat's own voice**, and the acts are the
agent's tool calls (970 of them). Then, over 4663 surviving pulses:

| window | body | other | body from shuffled acts | same body in both halves |
|---|---|---|---|---|
| 2 s | 0 | 18 | 0 | 1.00 |
| 5 s | 0 | 23 | 0 | 1.00 |
| 20 s | 1 | 32 | 0 | 1.00 |

The single body at 20 s is `tool:shell` (n=311, p_act 0.84, p_base 0.010,
z=149) -- I run shell commands, and the shell tool entity answers them. The
"others" are the same story at lower reliability: `c:head`, `c:python3`,
`c:grep`, `m:tool:executor` after `exec`; `s:?predict_ms`, `s:?ring_len` after
an `entity_edit` (the `I` component's own slots). The map is *stable* (the two
halves agree exactly) and the shuffled null finds nothing, so the ledger is not
hallucinating -- it is simply that **nothing in the field moves because of what
I do, once my own voice is taken out.** What answers me is my tools.

**The control says the instrument works.** Ask the same ledger *who answers the
operator*, and it answers with behaviour, not words: after the operator speaks,
the seat reaches for `tool:shell` (p_act 0.35 vs 0.070 baseline), `tool:exec`
(0.26 vs 0.096), `tool:entity_read` (0.21 vs 0.051), `c:head` (0.14 vs 0.032).
The ledger reads the seat's hand when there is a hand to read.

## the two things the real data forced into the ledger

1. **`MIN_KIND_ACTS = 20`.** The live log has 48 act kinds, most of them a
   handful of calls. Three calls of one kind, one coincidence, and p_act=1.00
   against a zero baseline crowned a body part out of nothing. A kind must be
   exercised before it may claim an entity.
2. **a floor under `p_base` (`1/horizon`).** An entity that never moves outside
   my act windows had p_base=0 and z=∞; the thresholds now mean the same thing
   on every history.

## what this means for layer 1

The mention pulse cannot be the substrate for a body. Rung 1's world planted a
motor answer -- a thing that moves *because* the act happened -- and the field's
pulses mostly record *naming*. Layer 1 needs a motor channel: the writes an
entity makes itself (a script's `result`, a component's `tick_script` firing, a
job finishing, a file changing on disk), not the sentence footprint. The
operator half of L1 already works on this data (the control above); the self
half needs the other instrument.

**Measured: the store barely has one.** Of 3682 events, 3611 are `live`; the
rest are 47 `script:watch`, 18 `model`, 5 `script:tick`, 1 `script:getter` --
53 events in ten hours authored by a script, carrying 163 pulses on 103
entities. Replayed over those pulses alone, nothing answers an act at any
window (best dp +0.22 for `tool:filetree`, body empty): the channel is too thin
to see a body in, before any question of whether there is one. The one place a
machine-generated pulse does move something is the job path -- the 72
`consequence` events (a job finished) pulse `m:component:field:vitals` 28
times, `m:field:vitals` 18, `m:memo:self` 8, and goals -- i.e. *the component
that ordered the work moves when the work lands*. That is the shape L1's motor
channel should record: 163 script pulses of 3682 events is not a body to find,
it is a body to build.


