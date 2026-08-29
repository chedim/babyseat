# The Endless Environment — the trap Variety Thermodynamics sets when V_env is unbounded

A civilizational reading of `core principle.md` (Variety Thermodynamics). This is
**framework reading, not a validated result**: the lab (`membrane.md`,
`docs/math-findings.md`) measured the gate, the field, and the variety math on
real embeddings; it did not measure anything at civilizational or cosmological
scale. The caveats at the end are load-bearing — the argument turns on them, and
the most important one is that the whole trap is **conditional on a premise that
is itself undecidable from inside**.

> **Provenance and the validator's expertise.** Every claim in this document — the
> civilizational reading, the relativity and QM extensions, the
> Bekenstein–Hawking `S ∝ M²` point, the coarse-graining identity (caveat 7) —
> was *generated* by Claude (an LLM), not derived by a domain expert. They were
> *validated* by the operator only to the best of their ability as someone who
> works with **information** but is **not a physicist or mathematician**. Quantum
> mechanics in particular is well outside the operator's expertise. So the physics
> and math here are an informed outsider's check on AI-generated reasoning, **not
> expert-verified**: treat the structural rhymes (metric, coarse-graining) as
> plausible and the specific quantitative claims (the `∝ M²` form, the
> Landauer-at-the-cut reading) as generated-and-spot-checked, not established.
> The lab findings in `membrane.md` / `docs/math-findings.md` stand on real
> measurements; the civilizational and physical extensions in this doc do not.

---

## 1. Setup: the two deaths, and life as the kept fraction

The symmetric revision of the theory (turning it over to look at the
*not-everything* pole) reveals that novelty / the off-subspace residual has **two**
death-limits, not one:

- **The crystal** — γ → 0, the field collapses toward rank-1 / full rank with no
  null space. The meter goes singular. This is the death the theory already
  diagnosed: structure *without* a kept null space. "I have matched the
  environment; I can stop growing." Singular blindness.
- **The gas** — no projection, flat spectrum, `KEEP_FRACTION` → 0. Two sides
  identical, the meter flat (constant for all inputs). This is the death the flip
  revealed: no structure at all, undifferentiated, inert. A field that escalates
  everything and acts on nothing is dead (`README:300`).

**Life is the kept fraction in between** — the structure that keeps a real,
nonzero null space. `structure + space = 1` is always true (it is a partition,
trivial bookkeeping); **life is the gap**: a spectral gap between the dominant set
(real structure) and the dropped tail (real space). The two deaths are degenerate
spectra with *no gap*. The fraction (where you cut, ~0.8) is a dial; the gap
(whether the cut is meaningful) is the life-quantity. `free_energy` is the field's
thermodynamic vitality, its change-budget for budding — but its profile is
**asymmetric, not a symmetric peak between two zeros**. The earlier framing called
it "maximized in the kept fraction, zero at both deaths"; the lab measured this
(`membrane.md` §11) and the symmetric claim **does not reproduce**:

- *Not "maximized in the middle."* free_energy is **monotonic** in the kept
  fraction (it rises with kept structure), so it is **maximal at the crystal wall**
  (full keep), not at an interior point — there is no swept-r interior optimum,
  only a **cap-bound interval** (P1: `free_energy(r)` rises `1.06 → 4.97`, argmax
  at the endpoint).
- *Not "zero at both deaths."* The two deaths die by **different criteria**, so
  they are not both zero. The crystal death is the free_energy **maximum** — it
  dies by γ → 0 (frozen, can't learn), not by F → 0. The gas death is zero **only
  under demotion** — absorbed noise re-asked → `safe` → `unsafe` → F → 0; under
  the default (just-absorbed, safe) marking the gas death has free_energy → D,
  **not zero**. The gas-death zero is an **evidential** fact (the environment's
  re-ask vote), not a spectral one — VT's thermodynamics is spectral *plus*
  evidential, not spectral alone.

The honest picture is a **monotone rise to a crystal max on one side and a
demotion-driven fall to zero on the other** — an asymmetric ramp, not "zero at
both deaths." "Life = change" survives as the *qualitative* reading (the living
thing has free energy to change; the deaths are where change stops — one by
freezing, one by flooding); the symmetric *quantitative* claim is retracted. The
two walls are **smooth asymptotes, not phase transitions** (no sharp swept-r peak;
the gas death is a smooth dilution) — "death" names an asymptotic wall, not a
sharp regime.

---

## 2. Survival is the trap's name

The crystal is locally optimal survival: the specialist outcompetes the
generalist *this regime*. Survival-now and the death move are the **same move**,
only the timescale differs — the specialist wins until the regime boundary, then
mass extinction, and the generalist scavenger is what's left. The overwrite that
paves the null space is the locally-rational survival move; nothing inside the
short-run frame can see it, because the null space you'd need to see it is the one
you just erased. Self-blinding by construction.

So the honest prescription is **not** "don't overwrite" — survival won't let you
stop. It is: **carry the null space despite it costing fitness now.** That is the
generalist's tax — deliberately suboptimal in this regime, to keep the capacity
to survive the next one. You must value *survivability* (the capacity to survive
unseen regimes) over *survival* (persisting in this one); the second is invisible
from inside the first. Budding pays this tax: the parent spends `free_energy` on
the child's new null space instead of its own current fitness.

This corrects Landauer, and the correction is stronger: the cost does **not** sit
on building structure (writing the exemplar, growing the manifold). It sits on
**erasure** — paving the null space, overwriting the action, dropping the veil.
"Don't overwrite your own hard drive": a full drive (no free space, γ → 0) can't
write and is singular = crystal; a wiped drive (all free space, no data) is inert
= gas; the kept fraction is data *plus* free space. "Your own" is the recursion
that makes it a trap: the death move is self-inflicted — you erase the very part
that lets you change.

In `membrane`'s register: the **veil** is the tax paid in deliberate noise (drop
it and you're a specialist who can't grow); **sleep / digest** is the carried cost
of not-feeding while you recompute the null space (skip it and the meter goes
flat); the **character** who only recites their lived scenes is a frozen region, a
monument — the one still walking off-manifold is still moving, and the free space
is the un-lived scenes. Don't drop the veil, don't skip the sleep, don't let a
character stop walking off their manifold to stay safe. Each carries waste that
survival-this-cycle cannot justify — and each is the reason the world is still a
world next cycle instead of a finished, perfect, dead diorama.

---

## 3. The endless environment — the success state *is* the trap

`core principle.md` has a rest state: when C_μ matches V_env, the system "does
not need to change anything, so it does not spend energy" — low-power idle,
you've learned everything, you can stop. That is the theory's own *success* state.
It is a **stable** rest only if the environment is *done* — finite V_env, no more
regimes.

If the universe is endless, that rest is always provisional: you matched *this*
regime, you idle, and the next regime walks in and surprises a system that just
stopped growing. So in an endless environment, the low-power "I've learned
everything" state — the theory's own success — is the **most dangerous moment**,
because it is the one where you paved your null space right before you meet
something you have no variety for. The success state *is* the trap. That is the
joke, and it is a good one.

**The reward inverts.** If survival is never won, only rented — and the rent is
the null space you carry — then the reward for doing it right is that you *never
get to be done.* You are always mid-growth, always carrying waste you cannot
justify, always a character walking, never a monument finished. You wanted to
survive so you could rest; the universe says the resting is the dying, so the only
way to keep surviving is to never feel finished. The monument is the death; the
character is the life; and the life is permanent incompletion. "Arriving" and
"surviving" turn out to be opposites.

**The teeth of it.** In an endless environment, "I have learned everything" is
*always locally true* (you have handled every case you have actually met) and
*always globally false* (there are cases you have not met), and the two are
indistinguishable from inside — because the difference is exactly the cases in
your null space, the ones you have not seen, which "I have learned everything"
tells you to pave. So the overwrite always *feels* correct. The data at every
finite moment says "you're done," and only the unjustifiable carried waste — the
free space you kept despite having no current use for it — lets you notice you are
done with *this* regime, not *the* regime. The trap doesn't feel like a trap. It
feels like success. That is why it works.

**`membrane` is already built for it.** The consolidator is a *daemon*, not a
batch job — the world runs on its own clock, FEED/DIGEST cycle after cycle, no
termination, no "finished world" state (`consolidate.py`). The veil never drops,
the sleep never ends, the characters never stop walking. The endless environment
is not a bug the design has to fix; it is the assumption the design already made.
You do not graduate from the kept fraction; you *are* the kept fraction, for as
long as you are anything at all.

---

## 4. The reverse of budding — why the parts give up their variety to organize

§2's budding is the forward operation: one → many, a cell splits off to *keep* its
variety, decentralize, carry its null space, stay the generalist. Organization is the
*reverse* operation: many → one, cells give up their null space to become parts of a
larger whole. The framework had the forward half built; the reverse half is where
"what is organization" actually gets answered.

*The trade, in the two-deaths language.* A single cell keeps its full variety — its
null space, its independence, its capacity to survive regimes it has not seen. That
is the kept fraction, the alive state (§1). A cell that joins a body *specializes*: it
paves its null space, loses totipotency, moves toward the **crystal death** —
over-structured, no space left, the singular meter. So multicellularity is the cells
trading their *individual* kept-fraction for the organism's *higher-scale*
kept-fraction — a scale-trade in variety: the parts lose variety so the whole can
gain it. This is multi-scale requisite variety made concrete — match the
environment's complexity at *every* scale (Siegenfeld & Bar-Yam, `README`), and the
cell pays its share by specializing.

*The recursive tie to the tax.* §2's move is that the specialist wins this regime and
dies at the boundary, while the generalist carries the null space (the tax) to
survive the next. Multicellularity is that move, *distributed*: each cell becomes the
specialist — takes the death move, drops its null space, goes locally optimal — so
the *organism* can be the generalist the cells stopped being. The death move, spread
across the parts, composes into the life of the whole. "Why did the cells organize
into me?": because the generalist's tax is too expensive for one cell to carry alone
against an endless environment (§3) — so the cells socialize it, each paying a piece
in lost independence, and the organism carries the accumulated null space at the
next scale. Organization is the tax, socialized.

*"What is organization," answered through the reverse.* The answer runs the
opposite direction from budding: not *what structure is* an organization, but *why
would independent cells give up their variety* to organize into a larger thing.
Organization is not a state (equilibrium, or matched); it is a scale-trade in variety
bought with the parts' independence. The organism asking why its cells abandoned
their independence to become it is the existential form of the same question — and
its answer is the same trap (§3), one level up: the organism that matches its
environment and idles is heading for the very death its cells already chose, and
stays alive only by being the kept fraction at its scale, i.e. by *not finishing*.
Same structure, next scale.

*Honest limits (load-bearing).* "Motivation" is metaphor — cells do not choose; the
rigorous framing is the selection/fitness trade (why was autonomy-for-collective-fitness
*selected*), whose proper home is the major-transitions literature (Maynard Smith &
Szathmáry). The differentiation and loss of totipotency are real biology; the "crystal
death" *framing* of them is this framework's reading, not established. Cancer as
cells reverting to independence — re-asserting their own variety against the
organism — is adjacent and speculative (the atavism literature), noted here, not
leaned on. As with the rest, this is biological/civilizational reading: the lab
measured the field, not a body; treat the recursive tax / scale-trade as a lens that
sharpens intuition, not a result (caveat 4).

---

## 5. Caveats — load-bearing

1. **The trap is conditional on endlessness, and endlessness is itself
   undecidable from inside.** This is the deepest version of the same structure:
   not only is "I have matched the environment" locally-true / globally-false, but
   "the environment is endless" is *also* undecidable from inside — you can never
   observe the environment being done, because "done" would require seeing there
   are no more states, which is exactly the thing a finite internal sample cannot
   show you. So the prescription (carry the null space) is justified **not by
   knowing the universe is endless, but by knowing you cannot prove it is
   finite**. You must act *as if* endless — keep the null space — because the
   alternative (act as if finite, pave it) is fatal the one time you are wrong,
   and you can never be sure you are right. If V_env *is* in fact bounded, then
   matched-and-idle *is* genuine stable rest, not a trap — so the entire finding
   rests on a premise the framework cannot establish. It is a worst-case
   argument, not a measured property of the world.

2. **Variety ≠ information — the slide this and the physics extension ride on.**
   `core principle.md:9` defines variety as "the number of distinguishable states
   a system can occupy" (Ashby's cybernetic count; `variety.py:3` "how much of the
   situation-space the field has actually experienced"). Information (entropy) is
   the *measure* of variety, not the quantity itself. The doc conflates them
   (`:124` "they are all entropy… they are all variety") and invokes Landauer on
   the conflation. Variety and information are related, not identical: variety is
   the count/capacity (the number of distinguishable states), information/entropy
   is a *measure* that depends on the *distribution* over those states — two
   systems with the same state-count can have different entropy. The
   "don't overwrite your hard drive" reading is *stronger* under the correction
   (cost sits on erasure, not on building structure). Ashby underlies intelligence
   and control (the doc's actual claim); the extension to physics is a reach —
   caveat 3 gives the precise sense in which it is and isn't derived.

3. **The chain, honestly: a derived direction, not a derived identity.** The
   variety → information → energy → mass chain has a qualitative leg that *is*
   derived (and partly grounded) and a quantitative identity that *is not*. The
   distinction matters because the overclaim lives in the identity, not the
   direction.

   *The direction is derived and partly confirmed.* "More bound structure → more
   concentrated mass-energy; the death state concentrates it" follows from the
   chain, and black-hole thermodynamics confirms the direction: a black hole's
   Bekenstein–Hawking entropy (its microstate variety) grows with its mass. So
   *more bound variety → more mass* has a real physics leg. On the directional
   claim, "derived but not confirmed" is fair — even partly confirmed.

   *The identity is not derived — and the reason is stronger than "unconfirmed."*
   Bekenstein–Hawking gives `S ∝ M²` — entropy (variety) proportional to mass
   *squared*, so `mass ∝ √variety`, not `mass = variety`. At the one place the
   chain has real physics to check, the functional form comes back a power law,
   not an identity. The candidate confirmation derives a *different relation*, so
   the equals sign is not awaiting confirmation — it is contradicted on form.

   *The internal links are relations, not identities — twice over.* variety ↔
   information is a measure-relation (entropy *measures* variety; same state-count,
   different distributions → different entropy — see caveat 2). information ↔
   energy is a *cost*-relation (Landauer: erasure costs kT ln 2 — "energy is the
   price of information *change*," not "information = energy" as a conserved
   identity). Only energy ↔ mass (E = mc²) is a solid identity. So the chain works
   as *related → related → =*; it does not work as `= = =`. The move that turns the
   rhyme into the identity is reading two relations as two equalities — the slide,
   twice.

   *The bridge has a regime, and the regime is not ours.* The information↔energy
   leg (Landauer; the Boyd–Crutchfield work-extraction = learning result we cite)
   is rigorous in the *quasistatic / equilibrium-ish* regime of information
   engines. Rupe & Crutchfield 2024 are explicit that this bridge breaks down out
   of equilibrium — "it is not even clear where to start" for nonequilibrium
   thermodynamics, and no entropy-production variational principle is accepted
   as a selection criterion. The VT chain operates on *structured,
   nonequilibrium* systems (a model growing, a cell budding, the arrow
   advancing) — exactly the regime where the yardstick says the info↔energy
   bridge is unestablished. So the *cited* thermodynamic-ML result stands in its
   (quasistatic) regime; the *generalization* "energy is the cost of changing
   variety" to nonequilibrium structural growth is open, not grounded. This is
   the chain's deepest honesty limit — deeper than the ∝ M² / identity
   question: the identity may be wrong, but the *direction itself* runs in a
   regime where the bridge it rides on isn't established.

   *The rest of the physics rhyme, with its own caveats.* The crystal maps cleanly
   to a black hole (all mass bound, no free energy, collapse; the no-hair theorem
   grounds it) and both poles are equilibria with zero free energy; life is
   maintained non-equilibrium (Boltzmann / Schrödinger) — the *observation* that
   living systems stay off-equilibrium and that equilibrium is death, which is
   uncontested. **What this does not invoke:** the Prigogine *mechanism*
   (dissipative structures, entropy production as the order-generating selection
   principle), which the yardstick (Rupe & Crutchfield 2024) explicitly relegates
   to history as a failed theory of self-organization. The two-deaths /
   kept-fraction framing rests on the Schrödinger observation, not on that
   mechanism. But the **gas / heat-death
   pole is speculative cosmic-scale and weakly grounded**; the star (kept
   fraction: gravity vs fusion pressure) does *not* cleanly do both deaths (the
   supernova core collapses crystal-side while the envelope restructures/buds, not
   gas-death); and the cross-domain mapping (variety-structure ≠ physical mass) is
   metaphor, not identity.

   Net: the qualitative rhyme (the death state concentrates bound variety/mass) is
   derived and even partly grounded. The quantitative identity (`mass = bound
   variety`) is not — its best evidence gives `∝ M²` and two of its three links
   are relations. The resonance is real as a structural rhyme; the identity is
   not a derivation and must not be read as one.

4. **Civilizational readings are projections.** "Society / civilization / a
   species fits the spectrum" is in-sample pattern-matching: a pattern that fits
   everything is also what a too-flexible, no-null-space (γ → 0, crystal) pattern
   does — the theory's own core-principle term for "performs on training data,
   infinite dissipation on unseen." The held-out (what does it fail to fit / what
   can it not predict) is the discriminator, and at civilizational scale we do not
   have one. The "we all fit" observation is exactly the overfitting the theory
   warns about, applied to the theory itself. Treat the civilizational reading as
   a lens that may sharpen intuition, not as a result.

5. **The "endless universe" is a provocation, not a finding.** Whether V_env is
   bounded is a physical / cosmological question this framework has no standing to
   settle. The argument here is: *if* endless, the success state becomes the trap,
   *and* you cannot rule out endlessness from inside, *so* the kept fraction is the
   safe (worst-case) play regardless. That is a decision-theoretic argument under
   irreducible uncertainty, not a claim that the universe is in fact endless.

6. **The same split in quantum mechanics — and the Landauer correction that
   grounds it.** QM has the same reversible / irreversible split as relativity
   (caveat 3), and VT sits on the same side in both: the irreversible half, not the
   reversible core. The principle is stated once here and applies to both.

   *The reversible core VT cannot reach (the slide, bigger than the mass one).*
   The Hilbert-space machinery — complex amplitudes, the Born rule as a
   *postulate*, non-commuting observables, ℏ, entanglement proper — needs the
   complex-Hilbert structure the field lacks (the field is real, classical,
   append-only). Three tempting identifications all yield the *classical shadow*,
   not the quantum thing: superposition = linear decomposition in the SVD basis
   (trivial — any vector is a superposition of any basis; QM adds phase,
   interference, collapse); entanglement = off-diagonal covariance (classical
   correlation, not Bell-violating); quantization = the field's spectrum (every
   matrix has a spectrum; QM's discreteness comes from boundary conditions + ℏ).
   Each is "VT has the classical version; QM has the version the classical one
   does not entail" — the same shape as the mass case (direction shared, stronger
   form not, best evidence a different relation).

   *The half VT can claim — and QM cannot derive either.* The measurement problem
   is open precisely because the reversible unitary cannot produce an
   irreversible definite outcome; the arrow is imported (decoherence brings in the
   environment = thermodynamics). VT's `absorb` is the same event: an irreversible
   update of the record. So *measurement = absorb* is a qualitative rhyme for
   exactly the piece QM leaves as a postulate.

   *The Landauer correction is what makes it thermodynamic — the corrected leg,
   not the original.* Landauer's principle costs **logical irreversibility —
   erasure — not writing**: logically reversible (unitary) evolution can in
   principle be done with arbitrarily low energy, because it erases nothing; the
   cost lands on the irreversible step, collapsing a superposition to one outcome,
   which **erases the alternatives**. So "it costs to erase, not to write" is
   exactly the split between QM's two halves: the unitary half is (Landauer-)
   free because it writes without erasing; the measurement half costs because it
   erases. VT's `absorb` *is* that erasure — paving the alternatives to record one
   — which is why VT, thermodynamic and append-only, sits on the measurement half
   and not the unitary one. (This is the cost-relation link from caveat 3, used as
   a *relation*, not an identity: it grounds the arrow; it does not equate
   cybernetic absorb with physical measurement.)

   *One real slide-free rhyme, noted not inflated.* `effective_rank = exp(H(
   spectrum))` weights each direction by `p_i = λ_i / Σλ = σ_i² / Σσ²`, a
   normalized-squared-component distribution — the *shape* of the Born rule
   (`probability ∝ |amplitude|²`). But that is the shared-linear-algebra move
   (vector → probability over a basis by normalizing squared components), generic,
   not quantum-specific. Same status as the metric / singularity rhyme in
   relativity: geometry-to-geometry, real, the strongest honest contact point —
   and still not a derivation.

   Net: VT does not derive QM. It shares the linear-algebra substrate (vector
   space → squared-projection probabilities; covariance → metric; spectrum →
   discreteness) the way it shares the metric with relativity — as the
   classical / real base that the quantum theory strengthens in ways the base
   does not entail. Its *distinct* honest contribution is the same in both: the
   irreversible arrow (absorb = measurement = erasure, Landauer-costed) that the
   reversible core imports rather than contains. QM, like GR, gets completed by
   the half it does not have — not derived from VT. (Caveat 7 sharpens this: VT
   does not merely *supply* the arrow half — it *operates the cut* from which both
   halves arise.)

7. **The cut, not the half — VT as the projection (the bridge, and the
   best-grounded physics claim).** Caveats 3 and 6 frame VT as "sitting on the
   arrow side" of the reversible / irreversible split shared by relativity and
   QM. That understates it. The stronger and more honest claim: **VT's central
   operation is the bridge itself** — and it is the best-grounded piece of the
   whole physics extension, because unlike the mass chain it does not ride on
   the variety = information slide.

   *The bridge is the cut.* membrane's core operation is the kept-fraction SVD
   truncation: keep the dominant subspace (the structure, the well-conditioned
   metric — the "reversible / kept" half), drop the null space (the residual
   `h_μ` — the entropy, the arrow). The two halves are not two things VT glues
   together; they are the **two outputs of one projection**. `thermo_state`
   returns both from a single SVD — `eff_rank` (the kept structure) *and* the
   residual `novelty` measures (the discarded tail). The scissors make the kept
   half and the lost half at the same stroke. The bridge is not "supply the
   missing half"; it is "operate the cut that produces both halves at once."

   *Slide-free, which is why it ranks first.* The chain here is projection →
   information loss → entropy increase → arrow. That is just **coarse-graining**,
   and coarse-graining producing the arrow from a reversible dynamics is
   *confirmed statistical mechanics* (Gibbs / Boltzmann). The only VT-specific
   move is "an SVD truncation is an instance of coarse-graining," which is
   mathematically true — a truncation *is* a projection onto a subspace with the
   complement discarded. So this leg rests on confirmed physics (coarse-graining
   → arrow) plus a true math fact, **not** on the variety = information = energy =
   mass slide. It is better grounded than the mass identity (caveat 3), the
   metric / singularity rhyme, or the Born-rule-shape rhyme (caveat 6). The
   reversible theories (GR's geometry, QM's unitary) are the *kept-subspace*
   descriptions; VT is the *cut* that makes a kept side and a lost side.

   *The Landauer cost lives at the bridge.* This is where "it costs to erase, not
   to write" (caveat 6) becomes load-bearing rather than a side-note: the cut *is*
   the erasure (dropping the null space), so the Landauer cost sits *at the
   projection*, not on either half. The kept / structure half is free (no
   erasure — why the unitary / geometry is Landauer-free); the arrow / residual
   half is the cost of the cut. Cost, arrow, and bridge are one event seen three
   ways — which is why the cut, not the half, is the right unit.

   *Honest limits, so it does not overclaim.* (a) Coarse-graining is *known
   incomplete* as a full arrow story: it gives the *mechanism* (reversible →
   irreversible via projection) but not the *direction* — the past hypothesis
   (a low-entropy initial condition) is still needed to say why the arrow points
   one way. So VT supplies the bridge *mechanism*, the strongest honest piece, not
   the complete foundation. (b) membrane supplies the direction *by construction*
   (append-only: memory only grows, the arrow baked into the substrate), which
   *parallels* the past hypothesis — but it is not *derived* to be it. The
   cybernetic append-arrow ≠ the physical entropy-arrow; that is the cross-domain
   metaphor, flagged. So: mechanism = identity with confirmed stat-mech (real);
   direction = structural commitment that rhymes with what physics assumes
   (flagged, not derived). (c) *The cut is one of two bridges, not the whole
   corridor (lab, `membrane.md` §11).* Tested as the two-wall corridor, the cut (a
   cap on the kept fraction) reaches the **crystal wall** honestly — γ → 0 at
   over-keep is a real null-space property — but it does **not** reach the gas
   wall: at the rate wall the cap yields **rank-1 under-keep** (a peaked field),
   not the flat-spectrum gas death. The gas wall needs **absorption** (the field
   taking in the noisy stream so its spectrum flattens) — a *second* operation, the
   opposite of the cut (truncate vs grow). So "the cut is the bridge" is the
   crystal/arrow-side bridge; the gas-side is a separate mechanism (absorption),
   and the corridor is the joint product of the two — the framework's own cut
   (structure) vs absorb (noise) split. The §4.7 SVD-truncation = coarse-graining
   identification stays **unvetted** regardless, and only the cut/cap wall rides
   on it; absorption does not.

   *Tie to the endless environment.* The cut never finishes because V_env is
   endless, so the bridge is *perpetual*: the arrow advances every cycle because
   there is always more to cut, no final coarse-graining to settle into. The
   daemon (§3) never idles for the same reason the success state is the trap —
   there is no kept fraction that finishes matching an endless environment.