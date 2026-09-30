# The global field — the commons of claims

Status: **commons on the entity field** (the `claims` collection, origin
`commons`), consensus promotion at night, the old JSON commons converted
once. The peers layer (registry + live ask) is unchanged.

## The principle

Local fields are the actors ("no central router" — a single oracle is
rank-1, one perspective, cannot absorb variety). The commons is
**read-mostly substrate data**, not a server: each membrane stays
sovereign, and the operator's hand is still the authority that writes it.
The thermodynamics lift one level: variety that exists in one repo is
*repo variety*; facts that reproduce across repos are *operator-world
facts* and earn the commons.

**What the commons holds: claims** — the unit the field already checks
against evidence (action / tests / state claims; see entity-field.md).
A note, a compact record, a model's prose — anything that can be trusted,
disputed or superseded. Raw tool traffic never promotes (it is evidence
for the local field, not an assertion).

## How claims reach the commons

1. **The operator's hand.** `membrane: remember globally: X` writes the
   claim; `[membrane-intercept]` marks it (pre-gate, audited,
   idempotent on text). Still the most trusted path.
2. **Consensus promotion (night)** (`claims.promote_commons`): the same
   claim text made by **≥ 2 distinct origins** in their own `claims`
   collections promotes into the commons with its origins list. A seat's
   contradiction on that text marks it **disputed** — the conflict rides
   with the claim, it is not erased.
3. **The one-time conversion** (`claims.convert_global_notes`): the retired
   JSON commons (`~/.membrane/global-field.json` — operator-curated notes
   only) became commons claims tagged `origins: ["global-field"]` once.

**Rebalancing = trust, not residency.** The old residency-drift (a
commons note "moving home" toward its heaviest user) is retired with the
exemplar field: a claim's trust is its evidence and its consensus;
provance (`origins`, evidence event keys, contradictions) is carried BY
the claim.

## Coordination (no election, no leader, by design)

The commons rows live in the shared scope, an origin of their own
(`commons`) inside the `claims` collection — every seat reads them, only
the night's consensus pass (or the operator) writes them. There is
nothing to elect.

- **Reads:** the ENTITIES block carries the commons claims near the ask
  (`consensus` / `disputed` marked); the recall intercept surfaces the
  same tags.
- **Writes:** idempotent on the claim's content key; disputes update,
  never overwrite (a contradiction ADDS its conflict).
- **Crash:** the commons lags reality by one night pass at worst; nothing
  is lost (claims are substrate rows, not a RAM file).

## Blast radius / failure modes

- Manifold mixing is bounded because the commons is **claims only**.
- `membrane: forget globally …` is scoped to the commons; a local
  `forget #N` never touches it.
- Secrets: the commons is operator- or consensus-curated; promoting a
  secret is an explicit act, audited, forgettable.
- Peer asks (`membrane/peers.py`) remain a separate lane: a live teammate
  answers from THEIR field over `/membrane/ask` (read-only, bounded) —
  any hit rides as `[membrane-intercept]` context, any failure degrades
  to the model path silently.

## Test

`python -m pytest tests/test_global_field.py -q` (the commons promotion +
conversion on the MemBackend fake).