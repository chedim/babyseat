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
from pathlib import Path

import numpy as np

from .buffer import BufferView
from .curiosity import assess, CURIOSITY_MIN
from .runtime import Membrane


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
        fresh: bool = True,
    ) -> None:
        self.tag = tag
        self.curiosity_on = curiosity_on
        self.curiosity_min = curiosity_min
        self.curiosity_keep_fraction = curiosity_keep_fraction
        self._cur_dup_sim = dedup_sim
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
        cell's own field (``self.membrane.store``), so the verdict is cell-specific."""
        return assess(vec, self.membrane.store,
                      curiosity_min=self.curiosity_min,
                      dup_sim=self._cur_dup_sim,
                      keep_fraction=self.curiosity_keep_fraction)

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
        return self.membrane.learn_pair(text, response)

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