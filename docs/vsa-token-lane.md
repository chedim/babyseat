# The VSA token lane (the rosetta transposition)

*Status: built, default-on, TDD-pinned (`tests/test_vsa.py`). The lane
supplements the cosine; it never rules it.*

## The idea

rosetta-check's lab treated each statement as something that CHANGES the
field into a new state -- the field's state represents the program after
all statements have "massaged" it. Membrane's turn was atomic: one pooled
mxbai vector per situation, order and per-token surprise collapsed into a
bag-of-meaning point. The token lane transposes the rosetta idea to chat:
each TOKEN is a transition; the turn's final hypervector state is the
turn after all its tokens have massaged it.

Membrane already held the DISCRETE half of this: `TokenTimeline`
(`membrane/token_timeline.py`, Phase T3) stores every
(context -> next-token) pair as a durable transition table, feeds the
thinker's typed walk, and backs off K=3 -> K=1. This lane is the
CONTINUOUS sibling: one capture pass feeds both structures (the same
tokenizer, `vsa.shared_tokenize` = `token_timeline.tokenize`), and the
field cell remains the truth for both.

## The pieces

### The codebook (`VSACodebook`)

`V_tok = sign(R @ e_tok)` -- R a fixed seeded Gaussian (D=8192 x 1024,
seed 5), e_tok the BARE token string's embedding through the seat's own
Ollama embedder, lazy on first sighting, persisted at
`<data-dir>/vsa-codebook.json`.

THE ISOMETRY: the codebook is not random -- it is the embedder's geometry
binarized. Hamming distance in the codebook approximates cosine in
embedding space (measured: cat/dog 5% apart, cat/database 51%), so the
lane has pretrained semantics from day one with zero marginal cost after
the first embed per token type. The same (model, dim, seed) on every seat
yields the same codebook -- the shared substrate stays coherent
cross-origin, guarded by the id stamp.

No embedder (Ollama down): deterministic hash vectors -- no pretrained
semantics, the lane still binds, just weaker recall. Degrade, never fail.

Roles are seeded-random and STRUCTURAL (not semantic): the rosetta design
carries over unchanged.

### The recurrence (the "massage")

THREE components in one weighted-majority state (calibrated in tests):

- BAG: each distinct token once (paraphrase recall -- the isometry's
  cosine preservation).
- RECENCY ROLES: each token xored with the slot permuted by its distance
  from the END of the turn (order + tail dominance: weight 0.75^distance
  -- the forgetting).
- ADJACENT PAIRS: token_i x token_i+1 at a distinct offset (local order
  structure; a reordered stream breaks every pair, the state moves).

Measured property set (the tests pin them): deterministic; same tokens
reordered -> the state moves but stays nearer than unrelated text;
paraphrase nearer than unrelated; near-substitution nearer than
far-substitution; the tail dominates a long stream (crosstalk bounded by
the 128-token window).

`pack_state` -> base64 of the packed bits: ~1 KB/state at D=8192.

### The row stamp

`extra["vsa"] = {"cb": <codebook-id>, "s": <packed-state>}` rides the
exemplar. The wire schema already tolerates unknown keys
(`_rec_from_wire` preserves them; `_scm` versioning) -- non-breaking,
JSON and couchbase both carry it. Rows without the stamp (all legacy
rows) simply ride cosine-only.

### The dual lane (supplement, never replace)

- `ExemplarStore.vsa_top_k(lane, q_state, k)`: Hamming similarity over
  same-codebook rows; other rows invisible.
- `recall_merged(..., vsa_lane=..., vsa_query=...)`: rows whose token
  state AGREES with the query (Hamming-sim >= 0.70) take a small bonus
  (w_vsa=0.08 -- cosine-primary; the E4 lesson: projections floor rank,
  a quantized geometry informs, never rules). A Hamming-far row is
  never penalized.

### The advisory surprise

`VSALane.surprise(text)`: how far the live trajectory diverges from the
recent ones (0.0 = a shape just seen). The gate stamps it on the verdict
(`vsa_surprise`), the audit records it, and the ephemeral tier's
plasticity scaling modulates in the same direction as the cosine sim
(a seen token-shape reads duller -> shorter TTL; a divergent trajectory
reads novel -> longer). A verdict NEVER flips on this lane.

## The covenants

- **Dual-lane covenant**: mxbai cosine stays primary. The lane is a
  second sense organ on the existing skeleton, not a replacement.
- **Degrade, never fail**: no embedder -> hash vectors; any lane error
  never touches the turn (every seam is try/except).
- **One capture, two readouts**: the shared tokenizer feeds
  TokenTimeline (counts) and this lane (geometry) -- the fn-stamp bridge
  (actuation cells) and the chat-capture feed (teed pairs) both run.
- **Derived structure**: the field cell remains the truth; the lane and
  the timeline are both projections of it.
- **Coherence**: `codebook_id` = hash(model, dim, seed, scheme), stamped
  on every state; recall compares only same-id docs; a forked id rides
  read-only until re-encoded.
- **Kill switch**: `MEMBRANE_VSA=0` disables the lane end to end
  (`VSALane.make` returns None, no stamp, no bonus, no signal).
- **Lab isolation**: the codebook persists only next to a real field --
  lab paths (the `_is_lab_field_path` guard) skip persistence.

## What was measured before building (the honest calibration)

The first recurrence (pure recency-roles) failed its own order test:
3-token micro-strings moved only ~4% under reorder -- correlated tokens
average toward the same majority. The shipped shape (bag + roles + pairs)
was chosen by measurement: order 19% on 6-token turns (vs 4% at 3
tokens -- order structure grows with length), paraphrase 37% (nearer
than the 50% unrelated floor), tail dominance 18%-vs-43%, prefix
similarity ~0.60 (the surprise baseline). The test bars are calibrated
to these numbers, not guesses.

## Not built (deliberately, per the operator's call)

- Gloss distillation (LLM-written one-line senses for polysemous
  project vocabulary, quantized into the codebook): revisit only if the
  recall probe shows polysemous-token pain.
- Replay-by-continuation (generate from stored trajectories -- the
  dreamer's lane): a separate experiment; the TokenTimeline walk
  (thinker.py) is the discrete forerunner.