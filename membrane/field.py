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
import os
import tempfile
from dataclasses import dataclass, asdict, field
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
    # character ids this scene belongs to (the [[cast]] ledger). Empty for legacy
    # exemplars and chat-learned turns; populated by story-fed growth (the
    # chronicle scenes). Backward-compatible: old field records lack the key ->
    # Exemplar(**rec) falls back to this default.
    tags: list[int] = field(default_factory=list)
    # True when this scene was authored by the cell's OWN n-gram voice (the
    # cells-act chronicle / --speak ngram path), NOT by the LLM. Such beats are
    # absorbed into the field (so the world still grows + recall stays rich) but
    # EXCLUDED from the n-gram's training corpus + the cells-act seed -- otherwise
    # the n-gram trains on its own stammer and spirals to gibberish. The LLM is
    # the teacher at seed time; the n-gram speaks only from trusted (LLM-seeded)
    # material. Backward-compatible: old records lack the key -> trusted by default.
    self_authored: bool = False

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
        # set by load() when a corrupt field could NOT be quarantined: save()
        # then refuses to write, so it never clobbers the unrecovered file.
        self._quarantine_failed = False

    def append(self, ex: Exemplar) -> None:
        self.exemplars.append(ex)

    def forget(self, indexes) -> int:
        """Operator-given deletion: drop the exemplars at ``indexes``.

        The store is append-only for EARNED history -- the gate, the compactor and
        the boundary paths only ever add cells or mutate the evidence counts;
        nothing in the learning loop may remove a cell. The OPERATOR stands
        outside that promise: the same authority that injects entropy may withdraw
        what it gave, and an explicitly commanded forget (``membrane: forget ...``,
        the skills in .opencode/skills/, shipped as membrane/skills/) removes cells
        outright. Nothing else may
        call this. Persists nothing here -- the caller saves.

        Returns the number removed (out-of-range indexes are ignored -- the
        compactor may have renumbered between listing and dropping)."""
        idx_set = {int(i) for i in indexes if 0 <= int(i) < len(self.exemplars)}
        if not idx_set:
            return 0
        self.exemplars = [e for i, e in enumerate(self.exemplars)
                          if i not in idx_set]
        return len(idx_set)

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
        """Atomic save: write to a temp file in the same directory, then
        ``os.replace`` it over the target. A crash mid-write leaves the old
        field intact (the rename is the atomic publish step) instead of a
        truncated JSON no future load can read."""
        p = Path(path)
        if self._quarantine_failed:
            raise RuntimeError(
                f"refusing to save over unreadable field {p} "
                f"(quarantine failed at load); move the file aside first")
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp",
                                   dir=str(p.parent))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump([asdict(e) for e in self.exemplars], f, indent=2)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str | Path) -> "ExemplarStore":
        store = cls()
        p = Path(path)
        if p.exists():
            text = p.read_text()
            try:
                recs = json.loads(text) if text.strip() else []
            except json.JSONDecodeError as e:
                # corrupt field -- QUARANTINE, don't discard. The file is moved
                # aside with the exact bytes preserved so nothing is destroyed;
                # the proxy starts empty and the next save() writes cleanly.
                # (Before: a warning + an empty start, which silently orphaned
                # every memory in the file against the append-only promise.)
                import sys
                import time
                quar = p.with_name(p.name + f".corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
                try:
                    os.replace(p, quar)
                    print(f"[membrane] warning: field {p} unreadable ({e}); "
                          f"quarantined to {quar}; starting empty", file=sys.stderr)
                except OSError as e2:
                    print(f"[membrane] warning: field {p} unreadable ({e}); "
                          f"quarantine failed ({e2}); proceeding WITHOUT saving "
                          f"over it", file=sys.stderr)
                    # mark the store read-only-ish: save() refuses to clobber
                    store._quarantine_failed = True
                recs = []
            for rec in recs:
                store.append(Exemplar(**rec))
        return store