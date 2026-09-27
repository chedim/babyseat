# The thermodynamic principle — "only variety absorbs variety"

The project's principle, in plain words: Ashby's Law of Requisite Variety
with the control-theory skin peeled off. Control theory's telos is
*anti-variety* — the regulator exists to **collapse** a disturbance's variety
into a controlled, low-variety output ("destroy variety"). The membrane
inverts the ethics: variety is not the threat to be contained, it is the
**life to be grown**. `free_energy` (safe variety) is the field's vitality —
a field with no safe variety is inert (it escalates everything, acts on
nothing: dead). Same law, opposite telos: control theory says "grow enough
control variety to *keep the output controlled*"; the membrane says "grow
enough field variety to *absorb more of the world*."

The quantitative backing — where these measures were run, what was settled,
and how one experiment corrected another — is
[math-findings.md](math-findings.md). The theory notes behind it:
*the endless environment* (the trap unbounded variety sets) and *the cycled
environment* (the self-looping closure) — kept in the sibling
`cortex-experiment` repo, not this one.

## In the code

(The knobs below are `Membrane(...)` constructor args in
`membrane/runtime.py`, driven by the `experiments/` rigs — the retired
proxy's `--variety-temp` / `--sim-min-floor` / `--nudge-sigma` / `--tag`
flags are gone; the seat's live gate dial is `--sim-min`.)

- **`effective_rank`** = the field's requisite variety, *measured* not
  assumed. A query off the experienced manifold (high `novelty`) can't be
  absorbed → escalate. Only a field with variety *in that direction* can
  absorb a query in it.
- **`free_energy`** = `(safe fraction)·effective_rank − ¼·(unsafe
  fraction)·rank` — the *one number* that says whether the field is getting
  richer or just bigger. Surfaced in `field state` and `membrane review`.
- **adaptive temperature** (`variety_temp=`, default off) is the field
  *spending* its requisite variety: `sim_min` eases toward `sim_min_floor`
  (0.70, the validated safe cutoff) as `free_energy` grows. "Only **safe**
  variety absorbs variety" — a junk field pays the disorder tax, keeps
  `free_energy` low, and stays conservative.
- **the veil** (`nudge_sigma=`, default 0 = identity) is the per-instance
  perturbation of the incoming situation embedding: Gaussian noise added then
  renormalized, applied to everything that transits this membrane. The
  forwarded prompt text is **never** garbled; only the field's representation
  is veiled. A low-variety workload can't grow a varied field on its own;
  the nudge injects the variety it lacks.
- **multi-veil** — "run multiple of them" is **decentralized**, not
  orchestrated. No central input, no live router (a central oracle is a
  rank-1 field — one perspective — and rank-1 cannot absorb variety). Each
  membrane is autonomous — own field, own audit, own nudge seed, own
  temperature — pulling the same public workload. Each rig membrane's
  tag names its own field/audit files. Comparison is **offline**, reading each membrane's
  saved audit afterward.

```bash
PYTHONPATH=. .venv/bin/python experiments/multi_veil.py   # four autonomous membranes, one workload; compare free_energy offline
```

## The gate, in one breath

Cosine is the primary gate; the null-space novelty is a narrow low-rank
specialist — it earns a role only on genuinely low-rank fields
(`effective_rank / N < 0.5`). Recall is momentum-skewed kNN, the context
block is MMR-deduped, and the confidence gate's calibration is the whole
subject of [math-findings.md](math-findings.md).