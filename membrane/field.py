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
            for rec in json.loads(p.read_text()):
                store.append(Exemplar(**rec))
        return store