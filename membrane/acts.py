"""THE SPEECH-ACT TABLE (Phase W): the quantized act vocabulary and the
transition table for membrane's closed-loop participation.

THE QUANTIZED ACT TABLE: a few dozen speech acts (not tokens, not fns --
the participatory vocabulary). Each act has a realization organ (the
cheapest sufficient one) and a trigger condition. The transition table
maps (context-situation → act) with counts, SQL-able, the same shape as
the token timeline but at act grain -- the baby's growing conversation
map.

THE SELECTION POLICY: the momentum cone applied to the act table -- the
current context (the EMA of recent turns) selects which acts are
appropriate; the transition counts order them; the speech budget gates
the fire rate. The act-table is LEARNED (promote/refute at act grain:
an act that leads to a productive turn is reinforced; one that is
ignored is demoted) -- the baby's conversation skills grow from lived
participation, not from being programmed.

THE REALIZATION ORGANS (per act, cheapest sufficient):
  serve-receipt   -- the receipt lane (deterministic, byte-exact)
  report-state    -- the state renderer (template, no model call)
  note-pattern    -- the speech queue (the observation, pushed live)
  ask-operator    -- the review-pending lane (the baby asks)
  propose-draft   -- LLM or distiller, review-gated (the most expensive)
  stay-silent     -- no act (the default: silence is information)

THE CLOSED LOOP: observe (Layer 0 intake, running) → plan (select the
act from the context cluster) → speak (realize the act) → report (the
field's state update) → feedback (the operator's reaction = the label)
→ learn (the transition table). The loop is the baby in the room,
formalized.
"""
from __future__ import annotations

import time
from collections import defaultdict

# the quantized act vocabulary
ACTS = ("serve-receipt", "report-state", "note-pattern", "ask-operator",
        "propose-draft", "stay-silent")

# the realization organ per act (the cheapest sufficient)
_ORGANS = {
    "serve-receipt": "receipt-lane",
    "report-state": "state-renderer",
    "note-pattern": "speech-queue",
    "ask-operator": "review-pending",
    "propose-draft": "llm-or-distiller",
    "stay-silent": None,
}


class ActTable:
    """The speech-act transition table: (situation-sim → act) with
    counts. SQL-able (cb-native), incrementally fed at every turn
    (the operator's reaction is the label). The table IS the baby's
    conversation skills, growing from lived participation."""

    def __init__(self):
        self._table: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        self._context_totals: dict[str, int] = defaultdict(int)

    def observe(self, context_sig: str, act: str) -> None:
        """One lived transition: the operator's reaction (or the turn's
        flow) labels which act was taken in this context cluster."""
        d = self._table.setdefault(context_sig, {})
        d[act] = d.get(act, 0) + 1

    def select(self, context_sig: str, available: set[str] | None = None,
               rng_seed: int = 7) -> str:
        """Select the act for a context: the highest-count act in the
        cluster (the transition table's argmax), filtered by available
        acts. Falls to 'stay-silent' when the context is unlearned
        (the honest default: a new situation => no speech act)."""
        d = self._table.get(context_sig)
        if not d:
            return "stay-silent"
        if available:
            d = {a: c for a, c in d.items() if a in available}
        if not d:
            return "stay-silent"
        best = max(d, key=d.get)
        return best if d[best] > 0 else "stay-silent"

    def learn(self, context_sig: str, act: str, reward: float = 1.0) -> None:
        """The reinforcement: the operator's reaction (productive turn =
        +1, ignored = 0, re-ask = -1) adjusts the act's count."""
        d = self._table.setdefault(context_sig, {})
        d[act] = max(0, d.get(act, 0) + int(reward))

    def stats(self) -> dict:
        return {"contexts": len(self._table),
                "total_observations": sum(sum(d.values())
                                          for d in self._table.values())}
