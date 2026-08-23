"""The character ledger: who is in the world, and where each of them IS.

A character is NOT a row in a table; it is a *walking region* of the field. Its
``state_vec`` is the exponentially-weighted moving average of the chronicle scenes
tagged to it -- the tip of that walk. State lives in the field (the scenes are
real exemplars with ``tags``), and this ledger only makes the cast *addressable*
by name and tracks each character's rolling state.

Tagging is geometric: a scene is tagged to every character whose ``state_vec`` it
sits close to (cosine >= ``admit_threshold``). There is NO automatic admission --
a scene far from all characters is left untagged, so the cast stays short.
Characters are seeded once by the genesis chapter (the LLM names who it
introduced) and thereafter managed by hand from the menu (retire / pin).

This is the SPINE only: it lets characters emerge and walk. It does not yet steer
genesis (the drive), gate acts by consistency, or have cells author beats -- those
come later, on top of this addressable state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


@dataclass
class Character:
    id: int
    name: str
    state_vec: list[float]       # unit; the EMA of this char's tagged scenes
    anchor_idx: int | None       # the exemplar that introduced them (for "zoom to")
    born_cycle: int
    last_cycle: int
    scene_count: int
    pinned: bool = False


class Cast:
    """The cast list + the tagging/state machinery."""

    def __init__(self, decay: float = 0.6, admit_threshold: float = 0.40,
                 cap: int = 12, embedder=None) -> None:
        self.chars: list[Character] = []
        self._next_id = 0
        self.decay = decay
        self.admit_threshold = admit_threshold
        self.cap = cap
        self.embedder = embedder      # set by the Membrane after construction

    # ---- addressing ------------------------------------------------------
    def by_id(self, cid: int) -> Character | None:
        for c in self.chars:
            if c.id == cid:
                return c
        return None

    def _fresh_id(self) -> int:
        cid = self._next_id
        self._next_id += 1
        return cid

    # ---- admission -------------------------------------------------------
    def admit(self, name: str, vec, anchor_idx: int | None, cycle: int) -> Character | None:
        """Create a new character seeded by ``vec`` (a description or scene embedding).

        Respects ``cap``: a full cast refuses admission. The caller (genesis seed
        or a future menu action) is responsible for choosing the name.
        """
        if len(self.chars) >= self.cap:
            return None
        sv = _unit(np.asarray(vec, dtype=float)).tolist()
        c = Character(id=self._fresh_id(), name=name, state_vec=sv,
                      anchor_idx=anchor_idx, born_cycle=cycle,
                      last_cycle=cycle, scene_count=0)
        self.chars.append(c)
        return c

    # ---- the core: tag a scene to its cast + walk each tagged state -------
    def tag(self, vec, cycle: int) -> list[int]:
        """Tag ``vec`` (a scene embedding) to every character it sits close to,
        EMA-updating each tagged character's ``state_vec`` toward the scene.

        Returns the list of character ids tagged (possibly empty). Does NOT
        auto-admit: a scene far from the whole cast returns ``[]`` and the cast
        does not grow (keeps the list short; admission is seed/manual only).
        """
        if not self.chars:
            return []
        sv = _unit(np.asarray(vec, dtype=float))
        M = np.array([c.state_vec for c in self.chars], dtype=float)
        Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)
        sims = Mn @ sv
        tagged: list[int] = []
        for i, c in enumerate(self.chars):
            if float(sims[i]) >= self.admit_threshold:
                old = np.asarray(c.state_vec, dtype=float)
                walked = _unit(self.decay * old + (1.0 - self.decay) * sv)
                c.state_vec = walked.tolist()
                c.scene_count += 1
                c.last_cycle = cycle
                tagged.append(c.id)
        return tagged

    def retro_tag(self, store) -> int:
        """One-time, after the genesis seed: tag every existing exemplar to the
        cast from its STORED vec (no embed call), in field order (oldest first,
        so each char's ``state_vec`` walks forward to the most recent scene).

        Returns the number of exemplars tagged to at least one character.
        """
        n = 0
        for ex in store.exemplars:
            ids = self.tag(ex.vec, cycle=getattr(ex, "_cycle", 0) or 0)
            if ids:
                ex.tags = ids
                n += 1
        return n

    # ---- management (menu) ----------------------------------------------
    def retire(self, cid: int, store) -> bool:
        """Remove a character AND strip its id from every exemplar's ``tags``
        (its stars revert to untagged/neutral). Returns True if a character was
        removed. Pinned characters are refused unless ``force``."""
        c = self.by_id(cid)
        if c is None or c.pinned:
            return False
        self.chars = [ch for ch in self.chars if ch.id != cid]
        for ex in store.exemplars:
            if cid in ex.tags:
                ex.tags = [t for t in ex.tags if t != cid]
        return True

    def pin(self, cid: int) -> bool:
        c = self.by_id(cid)
        if c is None:
            return False
        c.pinned = not c.pinned
        return True

    # ---- persistence -----------------------------------------------------
    def to_json(self) -> list[dict]:
        return [asdict(c) for c in self.chars]

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=2))

    @classmethod
    def load(cls, path: str | Path, decay: float = 0.6,
             admit_threshold: float = 0.40, cap: int = 12, embedder=None) -> "Cast":
        cast = cls(decay=decay, admit_threshold=admit_threshold, cap=cap,
                   embedder=embedder)
        p = Path(path)
        if p.exists():
            text = p.read_text()
            try:
                recs = json.loads(text) if text.strip() else []
            except json.JSONDecodeError:
                recs = []
            for rec in recs:
                try:
                    cast.chars.append(Character(**rec))
                except TypeError:
                    continue
            cast._next_id = (max((c.id for c in cast.chars), default=-1) + 1)
        return cast