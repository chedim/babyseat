"""The agent Cortex: a variety-governed field over the agent's OWN decisions.

The membrane's existing gate learns the wrong thing for a coding agent: it embeds the
human's text turn (``_last_user``) and the assistant's prose as the "action". The
agent's actual decisions -- its ``tool_use`` blocks -- pass through the
``/v1/messages`` stream verbatim and unobserved. ``--cortex`` mode repurposes that
stream path to sense those decisions instead: each tool call is embedded, fed to a
temporal "decision field", and the field's variety is measured.

Two siblings, one union (the design doc):

* rosetta-check (Java) built the PER-DECISION gate -- an EMA "context field" over
  embedded events + a permanent reject-set policy + a POVM beta-calibrated decision
  + a Mahalanobis off-manifold detector (``cortex/Cortex.java``, ``cortex/FieldStore.java``).
  That math ports directly (embedder-agnostic -- swap "code statement" for "agent decision").
* membrane (``variety.py``) built the FIELD-LEVEL variety rosetta-check lacks --
  ``effective_rank`` / ``free_energy`` / ``orient_dirs`` / ``thermo_state``: the macro
  "is the decision field drifting to rank-1?" signal.

This module is the per-decision half (ported from ``Cortex.java:153-228``) plus a thin
``field_health`` wrapper over ``variety.py`` for the macro collapse signal. They cite
each other in docstrings (``membrane/gate.py``, ``membrane/embed.py``) but share no code
-- this is the first unification.

Phase 1 is SENSE only (passive monitor): the field is measured, nothing is gated. The
reject-set/POVM/Mahalanobis are ported as stubs (Phase 2 wires them to a judge
endpoint); an autonomous gate (Phase 4) is deliberately NOT implemented -- the safe
Cortex keeps the human in the loop. An autonomous gating Cortex is the
single-smooth-controller pattern the "It'll Pave Us" essay warns against.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .variety import free_energy, orient_dirs, thermo_state


def _unit(v: np.ndarray) -> np.ndarray:
    """L2-normalize (rosetta's ``Cortex.unit``). A zero vec stays zero, not NaN."""
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


@dataclass
class Decision:
    """One agent decision (a tool call) as the variety-field sees it.

    ``vec`` is the UNIT embedding of the decision text (tool name + input). ``safe`` is
    True in Phase 1 (every decision counts toward the field); Phase 2's reject-set marks
    a judged variety-collapsing decision ``safe=False`` so ``free_energy`` taxes it
    (the rosetta rule: a policy must not forget its own verdicts -- rejects are
    permanent). ``tool_use_id`` pairs the decision with its tool_result on the NEXT
    request, so the outcome (``result_text`` / ``is_error``) can be stamped retroactively.
    """
    vec: list[float]
    tool: str
    input_text: str
    tool_use_id: str = ""
    result_text: str = ""
    is_error: bool = False
    safe: bool = True
    turn: int = 0


class DecisionStore:
    """Append-only log of decision embeddings + the incremental EMA "context field"
    (rosetta-check's single centroid, maintained O(1)/decision -- NOT recomputed per
    decision; the SVD is paid only when ``field_health`` is read).

    Duck-types ``ExemplarStore`` (``.vectors()`` / ``__len__`` / ``.exemplars`` whose
    entries carry ``.safe``) so ``membrane.variety``'s ``effective_rank`` /
    ``free_energy`` / ``orient_dirs`` / ``thermo_state`` work UNMODIFIED over the
    decision field -- that macro "is the field collapsing to rank-1?" signal is the
    thing rosetta-check cannot compute. Phase 1 keeps every decision ``safe=True`` so
    ``free_energy == effective_rank``; Phase 2's reject-set sets ``safe=False``.
    """

    def __init__(self, alpha: float = 0.9) -> None:
        # EMA weight: field_after = alpha*field_before + (1-alpha)*unit(u). rosetta's
        # default carries context strongly (0.9); a smaller alpha forgets faster.
        self.alpha = float(alpha)
        self.decisions: list[Decision] = []
        self.field: Optional[np.ndarray] = None      # the EMA centroid (carried across runs)
        self._by_id: dict[str, Decision] = {}         # pending tool_result lookup (recent)
        self._dirty = False

    # ---- ExemplarStore duck-type (so membrane.variety works unmodified) -----
    def __len__(self) -> int:
        return len(self.decisions)

    @property
    def exemplars(self) -> list[Decision]:
        # alias: membrane.variety iterates ``store.exemplars`` and reads ``e.safe``.
        return self.decisions

    def vectors(self) -> np.ndarray:
        if not self.decisions:
            return np.zeros((0, 0), dtype=float)
        return np.array([np.asarray(d.vec, dtype=float) for d in self.decisions])

    # ---- the EMA nudge (ported from Cortex.java:153-228) -------------------
    def run(self, u: np.ndarray, surprise_threshold: float = 0.5
            ) -> tuple[float, float, bool]:
        """Nudge the context field by one decision (unit vec ``u``).

        Returns ``(novelty, similarity, surprise)``:

        * ``novelty``    = ||field_after - field_before|| (how hard the decision nudges
                           the accumulated context -- rosetta's ``delta`` norm).
        * ``similarity`` = cosine(u, field_before) (alignment with what came before;
                           low => the decision is foreign/surprising to the field).
        * ``surprise``   = similarity < surprise_threshold (the acquisition signal --
                           rosetta's STATEMENT-event surprise flag).

        The FIRST decision seeds the field (like rosetta's FILE event): the field is
        ``u`` itself, similarity 0, novelty 1 (the unit norm). The centroid is
        maintained incrementally -- no SVD per decision (the SVD is paid only when
        ``field_health`` is read, e.g. on a ``/cortex/state`` poll)."""
        u = _unit(np.asarray(u, dtype=float))
        if self.field is None or self.field.shape != u.shape:
            self.field = u.copy()
            return (1.0, 0.0, False)
        f = self.field
        fn = float(np.linalg.norm(f))
        similarity = float(np.dot(u, f) / fn) if fn > 1e-12 else 0.0
        scale = 1.0 - self.alpha
        delta = scale * (u - f)
        novelty = float(np.linalg.norm(delta))
        self.field = self.alpha * f + scale * u
        return (novelty, similarity, similarity < surprise_threshold)

    # ---- append + outcome pairing -----------------------------------------
    def append(self, d: Decision) -> None:
        self.decisions.append(d)
        self._dirty = True
        if d.tool_use_id:
            self._by_id[d.tool_use_id] = d
            # bound the pending map to recent decisions (ids are unique within a
            # session but a long session would otherwise grow it without limit).
            if len(self._by_id) > 256:
                self._by_id.pop(next(iter(self._by_id)))

    def stamp_result(self, tool_use_id: str, result_text: str, is_error: bool) -> bool:
        """Pair a decision with its tool_result (the outcome) on the NEXT request --
        the harness returns the tool_result as a user message before the model
        continues. Returns True if a pending decision was found and stamped.

        Phase 1 keeps the decision ``safe`` unchanged (free_energy == effective_rank).
        Phase 2 will mark a variety-collapsing outcome ``safe=False`` here, and a
        human-judged UNSUPPORTED verdict will push the decision into the reject-set."""
        d = self._by_id.get(tool_use_id)
        if d is None:
            return False
        d.result_text = result_text
        d.is_error = is_error
        self._dirty = True
        return True

    # ---- persistence (the Python analog of FieldStore) --------------------
    def save(self, path: str) -> None:
        """Persist the EMA centroid + the decision log as JSON. The centroid (``field``)
        is what makes the field resume across restarts; the per-decision vecs are kept
        so ``effective_rank`` (the macro signal) is continuous across restarts too.
        Phase 2 artifacts (rejects / covariance / povmBeta) land in this same file."""
        if not path:
            return
        try:
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
        except OSError:
            pass
        data = {
            "alpha": self.alpha,
            "dim": len(self.decisions[0].vec) if self.decisions else 0,
            "field": (self.field.tolist() if self.field is not None else None),
            "decisions": [
                {"vec": d.vec, "tool": d.tool, "input_text": d.input_text,
                 "tool_use_id": d.tool_use_id, "result_text": d.result_text,
                 "is_error": d.is_error, "safe": d.safe, "turn": d.turn}
                for d in self.decisions
            ],
        }
        try:
            with open(path, "w") as fh:
                json.dump(data, fh)
            self._dirty = False
        except OSError as e:
            print(f"[cortex] could not save field to {path}: {e}", file=sys.stderr,
                  flush=True)

    @classmethod
    def load(cls, path: str, alpha: float = 0.9) -> "DecisionStore":
        s = cls(alpha=alpha)
        if not path or not os.path.exists(path):
            return s
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[cortex] could not load field from {path}: {e}", file=sys.stderr,
                  flush=True)
            return s
        s.alpha = float(data.get("alpha", alpha))
        for rec in data.get("decisions", []):
            s.append(Decision(
                vec=rec.get("vec", []), tool=rec.get("tool", "?"),
                input_text=rec.get("input_text", ""),
                tool_use_id=rec.get("tool_use_id", ""),
                result_text=rec.get("result_text", ""),
                is_error=rec.get("is_error", False),
                safe=rec.get("safe", True), turn=rec.get("turn", 0)))
        f = data.get("field")
        if isinstance(f, list) and f:
            s.field = np.asarray(f, dtype=float)
        s._dirty = False
        return s


class DecisionField:
    """The live Cortex: holds a ``DecisionStore`` + the shared embedder, and turns one
    agent decision (an ``AgentEvent``) into a field nudge + a variety reading. This is
    the object the proxy keeps on ``srv.cortex`` when ``--cortex`` is on. The embedder
    is the world's ``OllamaEmbedder`` (shared) so decisions embed into the SAME vector
    space the world's field lives in.
    """

    def __init__(self, store: DecisionStore, embedder,
                 surprise_threshold: float = 0.5) -> None:
        self.store = store
        self.embedder = embedder                # an OllamaEmbedder (shared with the world)
        self.surprise_threshold = surprise_threshold
        self.last: dict = {"novelty": 0.0, "similarity": 0.0, "surprise": False}

    def observe(self, event) -> Optional[int]:
        """Embed one decision, nudge the field, append it to the store. Returns the new
        decision index, or None if the embed failed / the event had no text (the
        decision is skipped, not recorded -- a broken embed must not poison the field).
        The embedding is the DECISION text (tool name + input), never the outcome: the
        field measures the variety of *what the agent chose to do*, and an
        outcome-dependent embedding would let a failed call's trace masquerade as a
        distinct decision."""
        text = event.embed_text()
        if not text:
            return None
        try:
            vec = self.embedder.embed([text])[0]
        except Exception as e:
            print(f"[cortex] embed failed ({e}); decision not recorded",
                  file=sys.stderr, flush=True)
            return None
        novelty, similarity, surprise = self.store.run(vec, self.surprise_threshold)
        self.last = {"novelty": novelty, "similarity": similarity,
                     "surprise": surprise}
        u = _unit(np.asarray(vec, dtype=float))
        self.store.append(Decision(
            vec=u.tolist(), tool=event.tool_name,
            input_text=(event.input_text or "")[:512],
            tool_use_id=getattr(event, "tool_use_id", ""),
            turn=getattr(event, "turn", 0)))
        return len(self.store.decisions) - 1

    def health(self, max_k: int = 8) -> dict:
        """The macro collapse signal over this field (one SVD -- call on a poll, not
        per decision). Thin wrapper so the proxy reads ``cf.health()``."""
        return field_health(self.store, max_k=max_k)


def field_health(store: DecisionStore, max_k: int = 8, eps: float = 1e-9) -> dict:
    """The macro collapse signal: the field-LEVEL variety rosetta-check cannot compute.
    Wraps ``membrane.variety`` over the decision field. ``effective_rank`` drifting
    toward 1 means the agent's decisions have collapsed to a single direction -- the
    modal solution paving the world flat; a high, growing rank means the agent is
    keeping its options diverse. One SVD (inside ``thermo_state``); call this on a poll,
    not per decision."""
    n = len(store)
    if n < 2:
        return {"decision_count": n, "eff_rank": float(n), "free_energy": 0.0,
                "spectrum_entropy": 0.0, "rank_ratio": 1.0, "safe_frac": 1.0,
                "n_dirs": n}
    thermo = thermo_state(store, eps)            # one SVD -> eff_rank/entropy/free_energy/...
    dirs = orient_dirs(store, max_k, 0.5)        # the distinct directions (the nudge layer)
    return {"decision_count": n,
            "eff_rank": round(float(thermo.get("eff_rank", 0.0)), 3),
            "free_energy": round(float(thermo.get("free_energy", 0.0)), 3),
            "spectrum_entropy": round(float(thermo.get("spectrum_entropy", 0.0)), 3),
            "rank_ratio": round(float(thermo.get("rank_ratio", 0.0)), 3),
            "safe_frac": round(float(thermo.get("safe_frac", 1.0)), 3),
            "n_dirs": len(dirs)}


# ---- Phase 2 stubs (ported, not yet wired to intervention) ----------------
# These are the rosetta-check per-decision gate pieces, ported now so Phase 2 is
# WIRING (a /cortex/judge endpoint + a verdict), not porting. None are called in
# Phase 1; an autonomous gate (Phase 4) is deliberately NOT implemented here.

def max_cos(u: np.ndarray, reject_vecs: list) -> float:
    """Max cosine(u, unitRejects[k]); ``u`` unit. rosetta ``Cortex.java:255``. The
    reject-set is permanent: a policy must not forget its own verdicts, so rejects are
    not EMA-diluted (unlike the drifting context field)."""
    best = 0.0
    for rej in reject_vecs or ():
        rej = np.asarray(rej, dtype=float)
        if rej.shape != u.shape:
            continue
        s = float(np.dot(u, rej))
        if s > best:
            best = s
    return best


def povm_score(max_r: float, beta: float, temperature: float = 1e-3) -> float:
    """sigmoid((maxR - beta)/tau): the calibrated probability the decision matches the
    reject-set. Decoupled from the drifting field so standards do not erode.
    rosetta ``Cortex.java:237``."""
    tau = temperature if temperature > 0 else 1e-6
    return float(1.0 / (1.0 + np.exp(-(max_r - beta) / tau)))


def povm_beta(supported_unit_vecs: list, reject_vecs: list) -> float:
    """The 0.9-quantile of supported->reject max-cosine. NaN when uncomputable (no
    supported or no reject vecs yet). rosetta ``Cortex.java:298`` -- beta is where the
    'normal' mass meets the known-bad, so a decision that lands past it is suspect."""
    if not supported_unit_vecs or not reject_vecs:
        return float("nan")
    sims = sorted(max_cos(u, reject_vecs) for u in supported_unit_vecs)
    pos = 0.9 * (len(sims) - 1)
    lo, hi = int(np.floor(pos)), int(np.ceil(pos))
    if lo == hi:
        return float(sims[lo])
    return float(sims[lo] + (pos - lo) * (sims[hi] - sims[lo]))