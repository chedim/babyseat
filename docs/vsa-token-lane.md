# The VSA lane — entity keys and relations

*Status: the lane two this document describes is built on; the token-lane
recurrence it came from was retired with the exemplar field (git history
is the archive — see the last section).*

## What the VSA machinery is now

Every entity and every role on the field gets a **hypervector** derived
deterministically from its NAME — not from the embedder:

- `eids.entity_key(eid)` = the hash-token binding of the canonical id
  (`vsa.hash_token_vec("ek|" + eid)`, D=8192);
- `eids.role_key(role)` = hash-random (never the semantic codebook —
  near-equal strings produce near-equal vectors, which cannot encode
  order; that was the E25 lesson, below).

Deterministic keys mean every seat computes the same vectors for the same
entities with zero shared state: relations compose identically
cross-origin, which is what makes them shareable in the commons.

## Relations = composites (membrane/relations.py)

A (subject, relation, object) triple is encoded as

    bind(role_subj, key(A)) ⊕ bind(role_rel, key(r)) ⊕ bind(role_obj, key(B))

a bundle whose parts stay recoverable by unbinding the role. Stored in the
`relations` collection per origin: `{subj, rel, obj, source, n, ts,
evidence event keys, the packed composite}`.

**Why VSA and not the embedder:** mxbai is nearly order-blind for
relations — "A calls B" vs "B calls A" at cos 0.975 (structure-awareness
0.06). With random role vectors, order structure comes back: the E25 run
measured 0.28 → 0.58 when the hash-random roles replaced the semantic
codebook roles, and the code graph's `calls` triples read structure
correctly (membrane's own lane 0.80). Read-back is
`unbind_role(composite, role)` → a noisy estimate of the role's payload,
then `cleanup()` against the known entity keys (nearest key wins).

**Sources** (built and maintained automatically):
- the code graph: `call:` relations per def pair, `defined_in` per file,
  superseded when a file's code events are replaced;
- observations: `tool:edit edited f:x`, `*_failed` on error outcomes.

**Readers:** the ENTITIES block's "top relations" lines, and co-mention
answers in `related_entities`.

## The canonical id space (membrane/eids.py)

`canon(kind, surface, graph)` — every mention lands on `kind:norm`:

| kind | example |
|---|---|
| `f:` | `f:membrane/router.py` (repo-relative file) |
| `s:` | `s:membrane.router.Router` (module-qualified; a bare symbol resolves through the codegraph defs to its qualname, else `s:?name`) |
| `c:` | `c:git` (discourse commands count only in backticks / after `$ `) |
| `port:`, `m:`, `u:` | ports, models, urls |
| `tool:` | `tool:edit` |
| `p:`, `w:` | phrases, words (plural-folded) |
| `ctx:` | `ctx:session:<sid>` (the RAM-only session context) |

Legacy overlay keys (`membrane::files:x`, `Membrane::Component:…`) map
through `_LEGACY_KIND` to the same canonical ids — the entity layer and
the field share one id space, which is why `forget #N`, the watch layer
and the overlay docs all key on `canon_eid`.

`mentions_of(sentence)` picks the entities a sentence moves: **specific
kinds win over phrases, phrases over words, maximal phrases**.

## The determinism covenants

- `vsa.DEFAULT_DIM = 8192`, all lanes seed-hash the same way: the same
  (dim, seed, scheme) on every seat yields the same vectors — shareable
  without a shared codebook.
- Degradation: the lane never requires the embedder or Couchbase; binding
  is pure numpy.
- Lab isolation: nothing under `tests/` or the lab paths persists into a
  real cluster's collections (the `_is_lab_field_path` guard).

## What the token lane was

Before the entity field, this document described the **VSA token lane**: a
per-turn hypervector "massage" (bag + recency roles + adjacent pairs,
packed ~1 KB/state) riding exemplar rows, with seven consumers (shape
archaeology, loop fingerprint, shape-aware dedup, the order-shaped ballot,
thinker convergence, the shape-poke, the streaming prefix seam) and the
measured properties (order 19% at 6 tokens, paraphrase nearer than
unrelated at 37%, tail dominance 18%-vs-43%).

The exemplar field went away outright (stage 17 of the rewrite;
git history is the archive). What survived is the **deterministic
binding**: the entity keys are the lane's `hash_token_vec` applied to
canonical ids, and its role-binding insight — random role vectors, not
semantic codebook ones — is exactly why the relations composites hold
order. The whole-message "one vector per turn" shape itself was abandoned
(e18: tracking entities by their mentioning sentences distinguishes 68%
vs 59%).