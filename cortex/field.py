"""Append-only exemplar store: the agent's analogical memory.

An exemplar is a judgment the agent (or a batch judge) recorded:

    situation : str        normalized summary of the task/context (what was faced)
    action    : str        a REPLAYABLE structured action (tool calls / edit / decision)
    outcome   : str        what happened when that action ran
    vec       : list[float]  unit situation embedding (local Ollama, mxbai-embed-large)
    safe      : bool       is replaying this action low-risk (read / idempotent)?

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
        """Flip an existing exemplar's `safe` flag.

        The store stays append-only: no exemplar is added, removed, or rewritten --
        only this one mutable bit changes (the training loop uses it to mark a replay
        that did not satisfy, so the field escalates-with-hint next time instead of
        replaying the bad answer).
        """
        self.exemplars[index].safe = value

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