"""The cell: one autonomous Membrane instance that PULLS situations from a shared
buffer when its own curiosity says to.

A membrane is a collection of cells; a cell is one Membrane (its own field, its own
audit, its own veil seed, its own temperature) plus the PULL surface that makes it
an active, curiosity-driven intake rather than a passively-driven handler. This is
the multi-cell architecture ported out of the experiment (experiments/multi_veil.py
built autonomous veils directly against ``Membrane``) into the library.

DECENTRALIZED BY CONSTRUCTION (the anti-control guardrail): a cell holds a
``BufferView`` -- the READ-ONLY face of the shared buffer (membrane/buffer.py). It
can scan situations and pull the ones it is curious about; it CANNOT deposit (feed
the buffer). The "the membrane cannot feed itself / safe = given-not-taken"
guardrail lives in the type system: there is no deposit method on the view for the
cell to call, so the boundary is enforced by what the object CAN do, not by a rule
it could drop. No cell knows about other cells; the buffer has no routing logic.
Specialization is emergent -- each cell pulls its own subset according to its own
experienced manifold -- not assigned.

OBSERVER BY DEFAULT: a cell runs its Membrane in observer mode (never replay, always
forward + learn, audit ``would_absorb``). This is the "the membrane never authors
the response" guardrail: the cell's only intake path forwards to a real responder
and learns from the response -- it never synthesizes an answer. Replaying a coding
answer is unsafe (the right action depends on the full transcript, not a cosine
match), so cells build a field of accumulated, labeled VARIETY, not cached replies.

CURIOSITY-DRIVEN INTAKE (opt-in via ``curiosity_on``): when on, ``pull`` is GREEDY
over the buffer -- each step it takes the situation the cell is MOST curious about
(highest off-manifold score), grows the field, and re-evaluates. This grows the
cell toward its most unabsorbed direction first, then the next, self-extinguishing as
each direction gets absorbed. When off, the cell takes every situation (the
"indiscriminate" control -- the baseline that shows curiosity's contribution to
specialization). The conservative gate (gate.decide) is untouched either way;
curiosity is the pull decision, the gate is the replay decision.

This is stage 1 (the foundation). It is library-level + experiment-proven; the LIVE
proxy wiring (a ``--cells N`` mode that deposits from request handlers into a shared
buffer and runs a cell pool) is stage 1.5/2, gated on the layer-1 hatchery test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

from .buffer import BufferView
from .curiosity import assess, CURIOSITY_MIN
from .runtime import Membrane

# a cell is "mature enough to color" once its field has grown this many exemplars
# (a 1-exemplar field has a trivial dominant direction -- not a cone yet).
_MATURE_MIN_EXEMPLARS = 4


class Cell:
    """One autonomous Membrane instance + the curiosity-driven pull surface.

    Wraps a ``Membrane`` (built in observer mode, own field/audit/veil) and adds:
      - ``curious(text)`` / ``curiosity_score(text)``: the pull decision
        (membrane/curiosity.py), evaluated on THIS cell's veiled representation of the
        situation against THIS cell's field (field-relative, veil-relative).
      - ``take(text)``: absorb one situation (synthesize a request body, run
        ``handle`` -> observer forward + learn).
      - ``pull(view)``: greedily take the situations the cell is most curious about
        from a read-only buffer view, until none are curious.

    The cell does NOT hold the buffer's deposit path (only a ``BufferView``), so it
    cannot feed itself -- the feeding boundary is structural.
    """

    def __init__(
        self,
        tag: str,
        responder,
        field_path: str | Path,
        audit_path: str | Path,
        embedder=None,
        judge=None,
        sim_min: float = 0.85,
        consensus_min: float = 0.60,
        dedup_sim: float = 0.95,
        observer: bool = True,
        vague_filter: bool = True,
        variety_gate: bool = False,
        variety_temp: bool = False,
        temp_driver: str = "free_energy",
        temp_tau: float = 1.0,
        sim_min_floor: float = 0.70,
        nudge_sigma: float = 0.0,
        nudge_seed: int = 0,
        curiosity_on: bool = True,
        curiosity_min: float = CURIOSITY_MIN,
        curiosity_keep_fraction: float | None = None,
        orientation: np.ndarray | None = None,
        orientation_strength: float = 0.0,
        fresh: bool = True,
    ) -> None:
        self.tag = tag
        self.curiosity_on = curiosity_on
        self.curiosity_min = curiosity_min
        self.curiosity_keep_fraction = curiosity_keep_fraction
        self._cur_dup_sim = dedup_sim
        # ``orientation`` (stage 4, budding): a unit direction in embed space the cell is
        # biased toward -- inherited from a parent cell's distinct experienced direction
        # (see ``bud`` / ``variety.orient_dirs``). Acts on curiosity TWO ways, both
        # default-off: a HARD alignment gate (curious only if aligned beyond
        # ``strength/(1+strength)`` -- load-bearing for specialists) and a soft ranking
        # factor on the score. Biases the PULL decision only (curiosity), never the gate /
        # boundary / observer mode. ``orientation=None`` / ``orientation_strength=0.0`` ->
        # no bias (identity: the cell is a plain generalist, bit-identical to stage 3).
        self.orientation = orientation
        self.orientation_strength = orientation_strength
        # the veil seed, stored on the cell so ``bud`` can read it off the parent
        # (Membrane consumes it into an RNG it doesn't expose back; the cell is where
        # the operator-supplied seed is recoverable for inheritance).
        self.nudge_seed = nudge_seed
        # the budding cost ledger (stage 5, energy economy): the ``free_energy`` the
        # cell had when it last budded. A cell buds only when it has grown
        # ``bud_cost`` MORE safe variety since its last bud (``ready_to_bud``), so
        # budding is self-limiting -- a static environment grows zero variety -> zero
        # further buds -> no runaway. Starts at 0.0, so the FIRST bud needs
        # ``free_energy >= bud_cost`` (accrued enough safe variety to spawn). This
        # is in-memory accounting (the experiment / operator-triggered path); the
        # live proxy does not auto-bud, so persistence of this ledger across boots
        # is a later, autonomous-reproduction concern.
        self.last_bud_energy: float = 0.0
        # ``fresh`` controls the boot-time field/audit: the experiment starts each
        # cell from an empty field (delete any existing field+audit on construct, the
        # stage-1 default). The LIVE proxy passes ``fresh=False`` so the Membrane
        # instead LOADS the existing field (runtime.py:513 ``ExemplarStore.load``) and
        # the cell persists across boots -- a long-lived population that keeps growing
        # from where it left off, not one wiped every restart. The only deliberate
        # reach-in the operator keeps is a manual reset (delete the per-cell field
        # files, or boot fresh); there is no mid-run knob to wipe a cell -- the guardrails
        # (observer / can't-feed-self / curiosity) are not exposed as overridable flags.
        if fresh:
            for p in (field_path, audit_path):
                if os.path.exists(p):
                    os.remove(p)
        self.membrane = Membrane(
            responder=responder,
            field_path=field_path,
            audit_path=audit_path,
            embedder=embedder,
            sim_min=sim_min,
            consensus_min=consensus_min,
            dedup_sim=dedup_sim,
            judge=judge,
            observer=observer,
            vague_filter=vague_filter,
            variety_gate=variety_gate,
            variety_temp=variety_temp,
            temp_driver=temp_driver,
            temp_tau=temp_tau,
            sim_min_floor=sim_min_floor,
            nudge_sigma=nudge_sigma,
            nudge_seed=nudge_seed,
        )

    # ---- the energy economy (stage 5): when to bud + the cost ----------------
    def energy(self) -> float:
        """The cell's thermodynamic VALUE -- its accrued safe variety, one number
        (membrane/variety.py ``free_energy`` = ``safe_frac * effective_rank`` minus
        a disorder tax). This is the "energy" that funds budding: a cell buds when
        it has grown enough of it (``ready_to_bud``), and budding SPENDS it (the
        ``last_bud_energy`` ledger). Decentralized -- the cell measures its OWN
        field, no central scheduler decides who buds."""
        from .variety import free_energy
        return float(free_energy(self.membrane.store))

    def ready_to_bud(self, bud_cost: float, min_dirs: int = 1) -> bool:
        """Should this cell bud? True when it has grown ``bud_cost`` MORE safe
        variety since its last bud (``energy() >= last_bud_energy + bud_cost``) AND
        it has at least ``min_dirs`` separable experienced directions
        (``orient_dirs``) to orient a child toward -- so the bud is a real
        specialist, not a no-op unoriented child. ``last_bud_energy`` starts at 0.0,
        so the first bud needs ``energy() >= bud_cost``. The growth requirement is
        the self-limiting bound: a static environment grows zero variety -> the cell
        never re-crosses the threshold -> no runaway auto-budding."""
        from .variety import orient_dirs
        if len(orient_dirs(self.membrane.store, min_dirs)) < min_dirs:
            return False
        return self.energy() >= self.last_bud_energy + bud_cost

    # ---- budding (stage 4): inherited orientation -> a specialist -----------
    @classmethod
    def bud(cls, parent: "Cell", tag: str, field_path: str | Path,
            audit_path: str | Path, direction_index: int = 0,
            orientation_strength: float = 2.0, cos_sep: float = 0.5,
            nudge_seed: int | None = None,
            seed_size: int = 0, seed_align_min: float = 0.5) -> "Cell":
        """Bud a CHILD cell oriented toward the parent's ``direction_index``-th distinct
        experienced direction -- the inherited orientation that makes a specialist.

        DECENTRALIZED BY CONSTRUCTION (the anti-control guardrail, held): the
        orientation comes from the PARENT's OWN field geometry
        (``variety.orient_dirs`` -- one unit vector per mutually-``cos_sep``-apart
        region of the parent's field, i.e. the distinct directions the parent actually
        grew along), not from a central router assigning skills. A generalist parent
        (grew along several distinct directions) buds specialist children, each
        oriented toward one of those directions; the children diverge in SKILL
        emergently from the parent's own variety, with no central input -- the same
        architecture as the cell pool (no orchestrator), now across generations.

        Why ``orient_dirs`` (representative directions) and not ``principal_dirs``
        (SVD variance axes): a generalist's centered field has ~isotropic variance, so
        its principal directions are DEGENERATE (arbitrary rotations of the skill
        subspace) and point at no single skill. The parent's representative exemplars
        ARE ~its skill axes (each is one base direction plus veil noise), well-defined
        for any parent -- so a child oriented at one inherits a clean single-skill aim.

        The child starts BLANK by default (``seed_size=0`` -> ``fresh=True`` -> its own
        empty field, bit-identical to stage 4). With ``seed_size > 0`` (stage 5, energy
        economy) the child inherits a SEED: a slice of the parent's SAFE exemplars
        aligned with the chosen orientation (``variety.aligned_safe_slice`` -- the
        parent's proven-safe forwards, NOT a buffer harvest), written to the child's
        ``field_path`` and loaded by constructing with ``fresh=False``. So the child is
        born COMPETENT in its skill (a non-empty field, positive ``free_energy``) but
        still has room to grow its own variety (the seed is capped). This is sig 3
        (byproduct-not-extraction): the child inherits the parent's BYPRODUCT (safe
        variety it grew while serving), not an EXTRACTION. The child's veil diverges
        from the parent's (a new ``nudge_seed``) so its realized variety trajectory is
        its own, not the parent's replayed.

        BUDDING COSTS THE PARENT (the growth bound, stage 5): this call records the
        cost on the parent -- ``parent.last_bud_energy = parent.energy()`` -- so the
        parent must grow ``bud_cost`` MORE safe variety (``Cell.ready_to_bud``) before
        it can bud again. A static environment grows zero -> zero further buds -> no
        runaway auto-budding. ``seed_size=0`` (default) skips the seed and is
        bit-identical to stage 4; ``Cell.bud`` is still the only new method on Cell.

        ``direction_index`` selects which distinct direction (0 = the one most aligned
        with the field mean, i.e. a central real cluster). If the parent has fewer than
        ``direction_index + 1`` separable directions, the child boots UNORIENTED (the
        budding is a no-op -- the parent hasn't grown in that direction yet; this is the
        honest "not ready to bud" state, whose TRIGGER is stage 5).
        ``orientation_strength=0.0`` would likewise make the child a plain generalist
        (no alignment gate, no ranking bias -- identity).

        Guardrails are untouched by budding: the child is still observer-mode (never
        authors), still holds only a ``BufferView`` (cannot feed itself), still has no
        safe-mutator -- the orientation biases the PULL decision only (curiosity), never
        the gate / boundary / observer. Verified in ``experiments/multi_bud.py`` (sig 5)."""
        from .field import Exemplar, ExemplarStore
        from .variety import aligned_safe_slice, orient_dirs
        dirs = orient_dirs(parent.membrane.store, direction_index + 1, cos_sep)
        if len(dirs) <= direction_index:
            orientation = None             # parent has not grown in that direction yet
        else:
            orientation = np.asarray(dirs[direction_index], dtype=float)
        m = parent.membrane
        seed = (nudge_seed if nudge_seed is not None
                else int(parent.nudge_seed) + direction_index + 1)

        # SEED (stage 5, opt-in): the parent's safe exemplars aligned with the chosen
        # orientation, written to the child's field_path so the Membrane LOADS them on
        # construct (fresh=False). seed_size=0 (default) -> no seed, fresh=True ->
        # bit-identical to stage 4. The seed keeps the parent's veiled vec + safety
        # evidence as-is: the child inherits proven-safe forwards, byproduct not harvest.
        child_fresh = True
        if seed_size > 0 and orientation is not None:
            slice_ = aligned_safe_slice(m.store, orientation, seed_align_min, seed_size)
            if slice_:
                seed_store = ExemplarStore()
                for ex in slice_:             # copy: the child's field is its own now
                    seed_store.append(Exemplar(
                        situation=ex.situation, action=ex.action, outcome=ex.outcome,
                        vec=list(ex.vec), safe=ex.safe,
                        reask_count=ex.reask_count, contest_count=ex.contest_count))
                seed_store.save(field_path)
                child_fresh = False           # Membrane.load picks up the seed

        # THE COST (stage 5 growth bound): budding spends the parent's accrued free
        # energy. Record it so the parent must grow bud_cost MORE (ready_to_bud)
        # before it can bud again -> self-limiting, no runaway.
        parent.last_bud_energy = parent.energy()

        return cls(
            tag=tag,
            responder=m.responder,
            field_path=field_path, audit_path=audit_path,
            embedder=m.embedder, judge=m.judge,
            sim_min=m.sim_min, consensus_min=m.consensus_min, dedup_sim=m.dedup_sim,
            observer=m.observer,            # the never-authors guardrail -- inherited, do NOT flip
            vague_filter=m.vague_filter, variety_gate=m.variety_gate,
            variety_temp=m.variety_temp, temp_driver=m.temp_driver, temp_tau=m.temp_tau,
            sim_min_floor=m.sim_min_floor, nudge_sigma=m.nudge_sigma, nudge_seed=seed,
            curiosity_on=parent.curiosity_on, curiosity_min=parent.curiosity_min,
            curiosity_keep_fraction=parent.curiosity_keep_fraction,
            orientation=orientation, orientation_strength=orientation_strength,
            fresh=child_fresh,              # False -> load the seed; True (default) -> blank
        )

    # ---- differentiation (stage 6): the REVERSE of budding -------------------
    def differentiate(self, direction_index: int = 0, cos_sep: float = 0.5,
                      align_min: float = 0.5, gate_strength: float = 4.0
                      ) -> "np.ndarray | None":
        """DIFFERENTIATE -- the reverse of budding (the anti-bud): a cell cuts ITS
        OWN field down to one experienced direction and LOCKS that direction as a
        hard gate, committing to the specialist/death move that ``bud`` avoids.

        Where ``bud`` (the forward operation) keeps the PARENT whole and buds a
        specialist CHILD (oriented, seeded, a soft/default-off curiosity bias),
        ``differentiate`` makes the cell ITSELF the specialist. It selects the
        exemplars aligned with one of its own ``orient_dirs`` -- the SAME selector
        ``bud`` uses to seed a child (``variety.aligned_safe_slice``) -- but keeps
        ONLY the aligned slice and DROPS the rest, then raises
        ``orientation_strength`` so the hard alignment gate
        (``curiosity._aligned``) refuses off-subspace situations. The symmetry with
        ``bud`` IS the "reverse of budding": same selector, opposite retention.

        The orthogonal exemplars are paved -- the null space collapses, the field's
        ``effective_rank`` falls toward 1 (toward crystal death), and the hard gate
        stops the lost directions re-growing via curiosity. A standalone
        differentiated cell is then DEAD on its orthogonal complement: it escalates
        on the bases it used to cover (the §4 "specialist alone is dead" prediction,
        tested in ``experiments/multi_differentiate.py``). The organism (Phase 2)
        resurrects it by ROUTING those situations to a sibling that owns them.

        DECENTRALIZED BY CONSTRUCTION (held, as in ``bud``): the specialization
        axis comes from the cell's OWN field geometry (``orient_dirs`` -- its
        distinct experienced directions), not a central router. No external input
        picks the skill; ``direction_index`` only selects which of the cell's own
        directions to commit to.

        IRREVOCABLE ON THE FIELD (the difference from ``bud``): the dropped
        exemplars are gone (the ``trim_entropy`` reassign-then-save pattern,
        runtime.py:917) and the orientation is set STRONG, so the hard alignment
        gate blocks off-subspace situations from re-entering through curiosity.
        ``bud`` orients a blank child; ``differentiate`` cuts a grown cell.

        NOT READY -> None: if the cell has fewer than ``direction_index + 1``
        separable directions (``orient_dirs``), or nothing aligned with the chosen
        direction is safe, the cell has not grown enough to differentiate in that
        direction -- the honest no-op, mirroring ``bud``'s "not ready to bud"
        (line ~245). The field and orientation are left untouched.

        SELECTION, NOT PROJECTION (the open §4.7 question): the cut keeps the
        aligned exemplars and drops the rest -- unit-vec invariant preserved, no
        memory distortion, reuses ``aligned_safe_slice`` exactly. ``align_min`` is
        the calibration knob (how tight the cone = how far toward death: the kept
        field's effective rank falls toward 1 as the cone tightens). The safe
        filter is inherited from ``aligned_safe_slice`` (the specialist crystallizes
        around its PROVEN-SAFE aligned knowledge, shedding both the orthogonal AND
        the disproven). A low-rank PROJECTION variant -- the literal SVD
        coarse-graining of §4.7 -- is deferred: it tests that specific unvetted
        claim directly but distorts memory and breaks the unit-vec invariant; it is
        added later as a ``mode=`` parameter to compare death signatures.

        GUARDRAILS UNTOUCHED (held): observer mode (never authors), the
        ``BufferView`` boundary (cannot feed itself), and the gate are unchanged.
        ``differentiate`` mutates the cell's OWN field and orientation only -- the
        same surface ``bud`` touches. It adds no deposit path, no self-author, no
        safe-mutator. The reverse of budding opens no farm move."""
        from .variety import aligned_safe_slice, orient_dirs
        dirs = orient_dirs(self.membrane.store, direction_index + 1, cos_sep)
        if len(dirs) <= direction_index:
            return None                  # not ready: hasn't grown in this direction
        orientation = np.asarray(dirs[direction_index], dtype=float)
        # the SAME selector `bud` uses to seed a child -- but keep ALL aligned (a
        # commit, not a capped seed of max_n=8) and drop the rest (the cut).
        kept = aligned_safe_slice(self.membrane.store, orientation, align_min,
                                 max_n=len(self.membrane.store))
        if not kept:
            return None                  # nothing aligned+safe to keep (degenerate)
        self.membrane.store.exemplars = list(kept)     # the trim_entropy reassign pattern
        self.membrane.store.save(self.membrane.field_path)   # the cut is persisted
        # ---- keep the cast honest to the cut field (the stage-6 fix) ---------
        # Unlike ``trim_entropy`` (which only sheds UNTAGGED entropy ballast),
        # ``differentiate`` drops real character-TAGGED lived scenes. Each
        # Character's ``scene_count`` / ``last_scene`` / ``state_vec`` would
        # otherwise still describe the PRE-cut field -- and ``voice_for``'s
        # per-character n-gram cache is keyed on ``scene_count``, which the cut
        # does NOT change, so a cached voice would keep speaking from now-dropped
        # scenes while ``last_scene`` phantom-grounds the next genesis /
        # progression seed ("continue from this point" at a scene the field no
        # longer holds). Re-derive each character from the SURVIVING tagged
        # exemplars and POISON the voice cache so the next ``voice_for`` rebuilds
        # from the cut field. Defensive: the multi-differentiate experiment builds
        # cells with NO cast, so this block is a no-op there; it only matters
        # against the cast-bearing live world.
        cast = getattr(self.membrane, "cast", None)
        if cast is not None and getattr(cast, "chars", None):
            for c in cast.chars:
                lived = [e for e in kept if c.id in getattr(e, "tags", [])]
                c.scene_count = len(lived)
                if lived:
                    c.last_scene = lived[-1].action or c.last_scene
                    V = np.stack([np.asarray(e.vec, dtype=float) for e in lived])
                    m = V.mean(axis=0)
                    nrm = np.linalg.norm(m)
                    if nrm > 1e-12:            # the _unit(_mean) centroid pattern
                        c.state_vec = (m / nrm).tolist()
                # no surviving scenes -> leave state_vec/last_scene as last known
                # (the char is dead in this cell's field; on_stage freshness +
                # the voice_for distinct-actions gate handle the rest)
                cast._voices.pop(c.id, None)        # poison the stale n-gram cache
                cast._voice_size.pop(c.id, None)
            try:
                cast.save(self.membrane.cast_path)
            except (AttributeError, OSError) as e:
                print(f"[differentiate] cast re-save failed ({e})", file=sys.stderr)
        self.orientation = orientation
        self.orientation_strength = gate_strength      # the lock: hard gate refuses off-subspace
        return orientation

    # ---- the pull decision --------------------------------------------------

    def _situation_vec(self, text: str) -> np.ndarray:
        """The cell's veiled representation of a situation -- embed then apply THIS
        cell's veil. Curiosity is measured in the same space the field lives in
        (veiled), so it is field-relative AND veil-relative: two cells with different
        veils see different curiosity for the same situation."""
        vec = self.membrane.embedder.embed([text])[0]
        return self.membrane._nudge(vec)

    def _assess(self, vec: np.ndarray) -> tuple[bool, float]:
        """Curious? and how curious -- in ONE novelty computation, against THIS
        cell's own field (``self.membrane.store``), so the verdict is cell-specific.
        Threads the cell's inherited ``orientation`` (stage 4) into ``assess`` so the
        curiosity score is biased toward the cell's oriented skill."""
        return assess(vec, self.membrane.store,
                      curiosity_min=self.curiosity_min,
                      dup_sim=self._cur_dup_sim,
                      keep_fraction=self.curiosity_keep_fraction,
                      orientation=self.orientation,
                      orientation_strength=self.orientation_strength)

    def curious(self, text: str) -> bool:
        """Should this cell pull ``text``? Field-relative, self-extinguishing."""
        if not self.curiosity_on:
            return True                  # control: indiscriminate intake (take all)
        return self._assess(self._situation_vec(text))[0]

    def curiosity_score(self, text: str) -> float:
        """The off-manifold score for ``text`` (the greedy "most curious first" signal)."""
        return self._assess(self._situation_vec(text))[1]

    # ---- intake -------------------------------------------------------------

    def take(self, text: str, auth: dict | None = None) -> tuple[dict, dict]:
        """Absorb one situation: synthesize a single-turn request body and run the
        membrane's ``handle``. In observer mode (the cell default) this FORWARDS to
        the responder and LEARNS the exemplar -- it never authors a response. The
        situation enters the cell's field; the boundary (the user turn) is what made
        it available, not the cell.

        This is the BARE-intake path (no real response available): the experiment and
        any non-teed deposit land here, forwarding to the responder (StubResponder in
        the experiment / stage-2 live cells) and learning the (real situation, stub
        action) pair. Stage-3 teed intake uses ``take_pair`` instead -- see ``pull``."""
        body = {"model": "cell", "messages": [{"role": "user", "content": text}]}
        return self.membrane.handle(body, auth=auth)

    def take_pair(self, text: str, response: str) -> int | None:
        """Absorb a COMPLETE teed pair: learn ``(situation, real response)`` directly via
        ``Membrane.learn_pair`` -- NO responder call, NO model call, NO forward. The action
        is the real response the live proxy teed into the situation's ``meta`` and attached
        once the forward returned (membrane/buffer.py ``attach_response``).

        This is the stage-3 teed-intake path: the cell still PULLS (it chose this situation
        by its own curiosity), but what it absorbs is the real ``(situation, response)``
        the user actually received, not a stub. The feeding boundary stays one-way -- the
        response entered through the boundary (attached by the live path), never generated
        by the cell."""
        return self.membrane.learn_pair(text, response, src="user")

    def pull(self, view: BufferView, greedy: bool = True) -> int:
        """Pull situations from a read-only buffer view into this cell's field.

        GREEDY (default): each step, take the situation the cell is MOST curious
        about, grow the field, re-evaluate, repeat until no untaken situation is
        curious. This grows the cell toward its most unabsorbed direction first --
        the self-extinguishing growth engine. When ``curiosity_on`` is False (the
        indiscriminate control), takes every situation in order.

        Situations are embedded + veiled ONCE per pull (the cell's representation
        is fixed for the pull; only the field grows across steps), so a pull costs
        one embed per situation plus one novelty SVD per candidate per step.

        SELF-EXTINGUISHING ACROSS PULLS (the live pool re-pulls the same buffer on a
        cadence): a cell never re-takes a situation already in its own field. The
        guard is derived from the field, not a separate store, so it is correct
        across boots too (``fresh=False`` loads the field -> those texts are present
        -> skipped on the first pull after restart). This matters because the veil
        is stochastic per call (runtime._nudge advances a per-veil RNG): without this
        guard, the same situation re-veils to a fresh vector every tick and looks
        novel vs the field's earlier-veiled exemplars -> re-taken every cadence ->
        unbounded duplicate growth. The situation TEXT (stored on each exemplar) is
        the stable identity; the veiled vec is not. The stage-1 experiment pulled
        once per cell, so it never re-veiled a taken situation and never hit this.

        TEED vs BARE intake (stage 3): each situation's ``meta`` carries its own mode.
        The live proxy deposits with ``meta["pending"]=True`` and, once the forward
        returns, attaches the real response -> ``meta["complete"]=True``. So: a
        COMPLETE situation is taken via ``take_pair`` (learn the real action, no model
        call); a PENDING situation is skipped this tick (the response hasn't landed); a
        BARE situation (no ``meta`` -- the experiment / stage-2 deposits) is taken via
        ``take`` (forward to the stub responder), unchanged. The experiment deposits
        bare -> it stays on the ``take``/stub path -> its results are unaffected.

        Returns the count taken."""
        sits = view.situations()
        if not sits:
            return 0
        vecs = [self._situation_vec(s.text) for s in sits]
        # already-absorbed situation texts -- the cross-pull self-extinguishing guard.
        absorbed: set[str] = {ex.situation for ex in self.membrane.store.exemplars}
        taken_idx: set[int] = set()
        count = 0
        while True:
            best_i, best_score = -1, -1.0
            for i, v in enumerate(vecs):
                if i in taken_idx or sits[i].text in absorbed:
                    continue
                # TEED (live) path: a situation deposited but whose real response hasn't
                # landed yet is PENDING -- skip it this tick ("not ready", not "not
                # curious"); the cell takes it next cadence once the proxy attaches the
                # response (complete). Bare deposits (the experiment / stage-2 path) have
                # no "pending" meta -> never skipped here -> take the situation immediately.
                if sits[i].meta.get("pending") and not sits[i].meta.get("complete"):
                    continue
                if self.curiosity_on:
                    curious, score = self._assess(v)
                    if not curious:
                        continue
                    if greedy:
                        if score > best_score:
                            best_score, best_i = score, i
                    else:
                        best_i = i
                        break
                else:
                    best_i = i
                    break
            if best_i < 0:
                break
            sit = sits[best_i]
            # dispatch on completeness: a COMPLETE situation carries the real response the
            # user received -> take_pair (learn the real action, no model call); a BARE
            # situation (experiment / stage-2) -> take (forward to the stub responder).
            if sit.meta.get("complete"):
                self.take_pair(sit.text, sit.meta.get("response", ""))
            else:
                self.take(sit.text)
            absorbed.add(sit.text)
            taken_idx.add(best_i)
            count += 1
        return count

    # ---- grazing the world field (the self-balancing observer organism) ----
    # The live pool does NOT graze the user-turn buffer (which re-embeds the whole
    # unbounded log every pull). It grazes the WORLD's field -- ``srv.membrane.store``
    # -- whose exemplar vectors are already computed. The cell applies ITS OWN veil
    # (``_nudge``, pure post-embed noise) to each pre-computed vec and lets curiosity
    # decide, so specialization is emergent (veil-driven) and the cost is numpy-only
    # (no embed call). Absorption goes through ``Membrane.absorb_exemplar`` (the
    # text-dedup guard that ``pull``'s ``absorbed`` set provides, but for an
    # ExemplarStore source instead of a BufferView).

    def graze(self, store) -> int:
        """Pull the WORLD's exemplars into this cell's field, curiosity-gated, with no
        embed call. ``store`` is an ``ExemplarStore`` (the world's, ``srv.membrane.store``)
        read as a snapshot (GIL-safe: one atomic read of ``store.exemplars`` then
        ``list()``). For each world exemplar: skip if its situation text is already in
        this cell's field (the text guard -- primary re-absorption defense, and it skips
        the curiosity SVD for the already-absorbed majority); otherwise veil the
        pre-computed vec via ``_nudge`` and let ``_assess`` decide. Curious exemplars are
        absorbed via ``Membrane.absorb_exemplar`` (which re-checks the text guard, appends
        the veiled vec, and saves). Self-extinguishing: as a cell's cone fills, fewer
        world exemplars are curious -> the cell's field self-bounds. Returns the count
        absorbed this graze."""
        world = list(store.exemplars)
        if not world:
            return 0
        already = {ex.situation for ex in self.membrane.store.exemplars}
        count = 0
        for ex in world:
            if ex.situation in already:
                continue
            veiled = self.membrane._nudge(np.asarray(ex.vec, dtype=float))
            curious, _ = self._assess(veiled)
            if not curious:
                continue
            idx = self.membrane.absorb_exemplar(
                ex.situation, ex.action, ex.outcome, veiled,
                ex.safe, getattr(ex, "tags", []), getattr(ex, "self_authored", False))
            if idx is not None:
                already.add(ex.situation)
                count += 1
        return count

    def dominant_dir(self, cos_sep: float = 0.5) -> np.ndarray | None:
        """This cell's dominant grown direction -- the first of its distinct
        ``orient_dirs`` -- or None when it is too immature to color (fewer than
        ``_MATURE_MIN_EXEMPLARS`` exemplars, or no separable direction). The
        differentiation signal the viewer colors by."""
        if len(self.membrane.store) < _MATURE_MIN_EXEMPLARS:
            return None
        from .variety import orient_dirs
        dirs = orient_dirs(self.membrane.store, 1, cos_sep)
        return dirs[0] if dirs else None

    def covered_dir(self, world_dirs, cos_sep: float = 0.5) -> int | None:
        """Which world direction this cell covers -- argmax cosine of its committed
        direction to ``world_dirs`` (the world's ``orient_dirs``). Uses the cell's
        ASSIGNED ``orientation`` (set at bud -- the gate that makes it a specialist)
        when present, else its emergent ``dominant_dir`` (an undifferentiated
        generalist). None when the world has no directions or the cell has no
        direction at all. The palette index for the viewer."""
        if not world_dirs:
            return None
        o = self.orientation
        if o is None:
            o = self.dominant_dir(cos_sep)
        if o is None:
            return None
        return int(max(range(len(world_dirs)),
                       key=lambda i: float(o @ world_dirs[i])))

    # ---- the hatchery-signature accessors (the test reads these) ------------

    @property
    def store(self):
        return self.membrane.store

    @property
    def audit_path(self) -> Path:
        return self.membrane.audit_path

    @property
    def observer(self) -> bool:
        """The never-author guardrail, as enforced by the membrane's observer mode."""
        return self.membrane.observer