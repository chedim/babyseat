"""The character ledger's STUB (the clean_main cut: the toy world's
cast is gone from the seat). The surface stays (runtime's call sites:
load/tag/walk_pov/pov_id/save) as a no-op so the field's story-fed
lanes degrade to self_authored=False / no tags -- the memories keep
landing, the POV narration falls back to the plain recall path."""

from __future__ import annotations


class Cast:
    """A no-op ledger: nothing tags, nothing walks, nothing persists."""

    pov_id = None

    @classmethod
    def load(cls, path, embedder=None) -> "Cast":
        return cls()

    def save(self, path) -> None:
        return None

    def tag(self, vec, cycle: int, text: str = "") -> list:
        return []

    def walk_pov(self, vec, cycle: int) -> bool:
        return False
