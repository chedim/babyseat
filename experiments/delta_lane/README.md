# the delta lane's evidence (2026-10-15)

Do embedding DELTAS name the kind of change on their own, across sensors the
recall never saw? (the cortex-experiment finding, tested on membrane's embedder)

    python delta_exp.py mxbai-embed-large 100          # rung 0 + rung 1, noisy phrasing
    python delta_exp.py mxbai-embed-large 100 stable   # rung 1, one phrasing per sensor
    python per_sensor.py                               # where the neighbours come from
    python dims.py                                     # accuracy vs bit width

Needs Ollama on :11434 with mxbai-embed-large. Synthetic sensors only (the
seat's own tick sensors were too flat to use); the real-history rung is next.

Rung 1, stable phrasing, 12 sensors, 1308 pairs, leave-one-sensor-out,
balanced accuracy (chance 0.333, permutation p99 ~0.366):

| feature | 1-NN | centroid |
|---|---|---|
| delta e(after) - e(before) | 0.631 | 0.412 |
| sign(R @ delta), 8192 bits | 0.628 | 0.408 |
| sign(R @ delta), 2048 bits | 0.640 | -- |
| [e(before), e(after)] | 0.460 | 0.388 |
| e(after) only | 0.363 | 0.372 |

Read: the subtraction isolates the change; the bits keep it; there is no
universal "up" axis (centroids ~ chance), so recall by neighbours; transfer
follows the number's shape (digits/magnitude), not its unit.

## what the field stores vs what rung 1 measured (`python obs_vs_state.py`)

The field's delta is taken against the entity's RUNNING state (EMA, GAIN 0.5,
seeded from the entity's name), not the previous reading:

| delta against | stable 1-NN | noisy 1-NN |
|---|---|---|
| the previous reading | 0.631 (bits 0.640) | 0.557 |
| the running state (what stage 1 stores) | 0.525 (bits 0.520) | 0.476 |

Still above the null (p99 ~0.366) but about a third of the gain is lost: the
running state blends the last few readings. Keeping the previous reading per
entity (one more vector per entity state) would recover it -- decided on the
real-history rung, not here.

## rung 3: real sensor history (`python rung3_ha.py <recorder.db> 20 150 [sig]`)

A read-only copy of a Home Assistant recorder DB (not in the repo). 20 busy numeric
sensors over 11 days (network throughput and power, wind speed, battery state of
charge and cell voltages, power use, position, anchor distance, an energy meter);
wrapping angles and duplicate series excluded; 3000 pairs; label = up/down/flat on
each sensor's own scale (the smallest third of its changes = flat). Chance 0.333.

| delta against | cross-sensor, raw strings | cross-sensor, 4 sig. figs | same sensor later, 4 sig. figs |
|---|---|---|---|
| the previous reading | 0.433 (bits 0.438) | **0.496** | **0.473** |
| the running state (what the field stores) | 0.372 | 0.380 | 0.423 |
| after-state only | 0.327 | 0.357 | 0.398 |
| before+after pair | 0.368 | 0.438 | 0.460 |

Much weaker than synthetic: real states change in the last decimals, and a text
embedder reads digits, not magnitudes (rounding to 4 significant figures helps;
30% of recorded "changes" were below display precision). For NUMERIC sensors the
change should be computed on the numbers; the running-state delta is near chance.

## rung 4: prose about an item (`python prose.py`)

16 items, every mention a random paraphrase with context noise; the classes are
built so the after-text alone cannot separate them ("started" and "stayed on" end
the same), which caps the after-state near 0.5. Leave-one-item-out, chance 0.25.

| | delta | delta bits | after only | before+after pair |
|---|---|---|---|---|
| started / stopped / stayed on / stayed off | **0.807** | 0.809 | 0.474 | 0.773 (centroid 0.939) |
| broke / fixed / stayed ok / stayed bad | **0.936** | 0.953 | 0.498 | 0.934 (centroid 1.000) |

Prose is where embedding deltas carry the TRANSITION.

## rung 5: two different facts about the same entity (`python aspects.py heavy`)

"the telephone is red" / "it was an old dial-type phone": 16 objects x 6 aspects,
every sentence carrying heavy object-specific context. Which two aspects do two
sentences about one object differ by (15 pairs, chance 0.067, leave-one-object-out):

| | 1-NN |
|---|---|
| abs(e1 - e2) | **0.896** |
| e1 - e2 | 0.803 |
| [e1, e2] | 0.381 |
| e1 + e2 | 0.308 |

Subtraction cancels the entity (and its backstory) and leaves what differs. (The
single-sentence aspect/value tests sit at the ceiling even raw, so they do not
discriminate and are not claimed.)

## the read

A mention decomposes as background + entity (stable across mentions) + what this
mention says (varies). The field already uses the STABLE part to find entities
(coherence of x - background residuals, accretion, births, junk); the delta bits
keep the VARYING part. Weak on sensor digits, strong on prose: transitions and
aspect differences survive across items never seen, and the 2048 sign bits keep it.
