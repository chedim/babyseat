"""Append-only exemplar store: the agent's analogical memory.

An exemplar is a judgment the agent (or a batch judge) recorded:

    situation : str        normalized summary of the task/context (what was faced)
    action    : str        a REPLAYABLE structured action (tool calls / edit / decision)
    outcome   : str        what happened when that action ran
    vec       : list[float]  unit situation embedding (local Ollama, mxbai-embed-large)
    safe      : bool       derived: (reask_count - contest_count) < demote_threshold.
                          Replaying a safe action is low-risk; an unsafe one is only a hint.
    reask_count  : int     how many times the environment re-asked after this was absorbed
                          (the usefulness authority voting "this replay did not satisfy").
    contest_count: int    how many times the model contested a demotion of this exemplar
                          (the correctness authority voting "my answer was right anyway").

`safe` is a TWO-AUTHORITY boundary, not a single oracle. A re-ask (the environment)
votes against; a contest (the model) votes for. The gate reads `safe` unchanged; the
counts are the evidence, and `safe` is recomputed from them -- so demotion is
evidential and reversible, not a hard one-shot flip that treats the environment as
infallible. Neither authority has a monopoly: each re-ask adds a vote, each contest
cancels one, and a contested-but-reasked exemplar holds the disagreement in the record
rather than resolving it one way silently.

The store is permanent: no decay, no EMA, no averaging. Within-session context is
the conversation; old lessons must not fade. It grows by append at judgment time;
at runtime the agent queries it by cosine to the current task's embedding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np


@dataclass
class Exemplar:
    situation: str
    action: str
    outcome: str
    vec: list[float]
    safe: bool = True
    reask_count: int = 0
    contest_count: int = 0

    def recompute_safe(self, demote_threshold: int) -> None:
        """Re-derive `safe` from the evidence: net votes against must reach the threshold.

        safe = (reask_count - contest_count) < demote_threshold. Default threshold 1 ->
        one un-contested re-ask demotes (the environment's single strong signal), but a
        contest cancels it (the model's veto). Raise the threshold for noise-robustness
        (require accumulated re-asks before demoting); the boundary itself is the counts.
        """
        self.safe = (self.reask_count - self.contest_count) < demote_threshold


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


class ExemplarStore:
    """Append-only store of exemplars; queryable by cosine to a query vector."""

    def __init__(self) -> None:
        self.exemplars: list[Exemplar] = []

    def append(self, ex: Exemplar) -> None:
        self.exemplars.append(ex)

    def set_safe(self, index: int, value: bool) -> None:
        """Manually override an exemplar's `safe` (the review surface / a supervisor).

        The store stays append-only: no exemplar is added, removed, or rewritten --
        only this one mutable bit changes. The normal path mutates the *evidence*
        (`add_reask` / `add_contest`) and recomputes `safe`; this is the escape hatch.
        """
        self.exemplars[index].safe = value

    def add_reask(self, index: int) -> None:
        """The environment votes against this exemplar (a re-ask after an absorb)."""
        self.exemplars[index].reask_count += 1

    def add_contest(self, index: int) -> None:
        """The model votes for this exemplar (it contests a demotion -- it stands by its answer)."""
        self.exemplars[index].contest_count += 1

    def repair_action(self, index: int, new_action: str, new_outcome: str = "pending") -> None:
        """Overwrite a DEMOTED exemplar's action with the corrected answer (repair, not abandon).

        The re-ask demotion path makes a wrong exemplar ``unsafe`` but leaves its wrong
        ``action`` in place; the learn-time dedup guard (``_find_dup``) then blocks
        appending the correct answer the model returned on the re-ask, so the slot
        escalates-with-hint on the WRONG action forever (abandonment). ``repair_action``
        overwrites the action with the corrected one -- the hint becomes correct, and
        IF the slot re-promotes it will replay the right answer.

        Evidence handling (the probation model -- NOT a clean reset):
          - ``reask_count`` is KEPT. The re-ask was against the old (wrong) action;
            after overwrite it is no longer a vote against the new action, but it is
            carried as PROBATION: "this slot has been wrong before, so the corrected
            answer must earn its way back." So ``safe`` stays False after repair --
            the slot does NOT replay yet (not the harsh immediate-trust of a cosine
            cache, which is the carelessness this boundary exists to avoid).
          - ``contest_count`` is RESET to 0. Any prior contests were about the old
            action and are moot. (In practice an unsafe dup always has contest_count 0
            anyway -- a prior ``contest()`` would have restored ``safe`` -- but reset
            is the honest invariant.)

        Re-promotion is NOT done here. It happens later, on the CONFIRM path: when the
        model stands by the corrected action on a subsequent escalation, that is a
        ``contest`` (``add_contest`` + ``recompute_safe``): the first contest cancels
        the probation re-ask -> ``safe`` flips to True -> the slot is replayable again.
        The environment keeps the final say: a further re-ask (reask=2) out-votes the
        single contest -> the demotion stands. That is the existing ``contest()``
        boundary rule, unchanged.

        The store stays append-only in COUNT: no exemplar is added or removed -- only
        this one exemplar's mutable fields (``action``/``outcome``/``contest_count``)
        change, the same fields ``add_reask``/``add_contest``/``set_safe`` mutate. Call
        only on an UNSAFE exemplar (a safe one is a proven-good answer -- never overwrite).
        """
        ex = self.exemplars[index]
        ex.action = new_action
        ex.outcome = new_outcome
        ex.contest_count = 0
        # reask_count is intentionally kept (probation); safe is intentionally left
        # as-is (already False on the unsafe dup). recompute_safe is NOT called here --
        # the confirm path (a later contest) does it when the model stands by the fix.

    def index_of(self, ex: Exemplar) -> int | None:
        """Identity lookup of an exemplar the gate recalled (``e is ex``).

        Uses identity, not dataclass equality: a near-duplicate exemplar would compare
        equal by fields, so ``==`` is unsafe here. ``top_k`` returns the actual objects
        from ``self.exemplars``, so identity holds.
        """
        for i, e in enumerate(self.exemplars):
            if e is ex:
                return i
        return None

    def __len__(self) -> int:
        return len(self.exemplars)

    def vectors(self) -> np.ndarray:
        """All exemplar vectors as an (N, D) array (empty -> (0, 0))."""
        if not self.exemplars:
            return np.zeros((0, 0), dtype=float)
        return np.array([e.vec for e in self.exemplars], dtype=float)

    def top_k(self, q: np.ndarray, k: int = 5) -> list[tuple[Exemplar, float]]:
        """The k nearest exemplars to unit query q, by cosine similarity."""
        if len(self.exemplars) == 0:
            return []
        V = self.vectors()
        qn = _unit(q)
        Vn = V / (np.linalg.norm(V, axis=1, keepdims=True) + 1e-12)
        sims = Vn @ qn
        k = min(k, len(self.exemplars))
        idx = np.argsort(-sims)[:k]
        return [(self.exemplars[i], float(sims[i])) for i in idx]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps([asdict(e) for e in self.exemplars], indent=2)
        )

    @classmethod
    def load(cls, path: str | Path) -> "ExemplarStore":
        store = cls()
        p = Path(path)
        if p.exists():
            text = p.read_text()
            try:
                recs = json.loads(text) if text.strip() else []
            except json.JSONDecodeError as e:
                # empty / truncated / corrupt field -- degrade to an empty store
                # rather than killing the proxy on startup. Nothing is lost: the
                # raw file is untouched, and the next save() overwrites cleanly.
                import sys
                print(f"[membrane] warning: field {p} unreadable ({e}); "
                      f"starting empty", file=sys.stderr)
                recs = []
            for rec in recs:
                store.append(Exemplar(**rec))
        return store