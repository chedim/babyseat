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
