"""The shared situation buffer: the passive, broadcast substrate cells pull from.

This is the live analog of the multi-veil experiment's workload file, made into a
structure. It is the decentralizing move: the buffer is PASSIVE DATA, the cells are
the ACTIVE party. Each cell independently pulls (takes into its own field) the
situations IT is curious about; the buffer never routes, never pushes, never decides
who gets what. There is no central orchestrator -- a central oracle would be a
rank-1 field (one perspective, me in the middle), and rank-1 cannot absorb variety.
Only a population of differently-curious cells can.

BROADCAST, NOT QUEUE: a cell taking a situation does NOT remove it -- other cells
can take it too. Each cell sees the whole buffer and pulls its own subset. This is
what makes specialization emergent rather than assigned: two cells pulling the same
buffer take different subsets (each according to its own curiosity / its own
experienced manifold), with no router dividing the work. A dequeue-and-remove queue
would be a central allocator in disguise (it decides who gets each situation);
broadcast keeps the buffer passive.

THE FEEDING BOUNDARY (a guardrail, structural here): only the external world -- the
proxy, fed by real user turns -- deposits situations. A cell has NO deposit path: the
``Cell`` (membrane/cell.py) holds a read-only view of the buffer, not the deposit
method. The membrane cannot feed itself, and the user can starve it at any time
(feeding is at the turn boundary the membrane cannot reach past). This is
"safe = given-not-taken" made concrete: the situations enter only through the
boundary, never self-generated. The curiosity function (membrane/curiosity.py)
decides what to take; the boundary decides what exists to take.

Stage 1 uses this in-process (the experiment); the live-proxy wiring (deposit from
request handlers, completion channels back to held sockets) is stage 1.5/2 and gated
on the layer-1 hatchery test going green. The structure is thread-safe from the start
so that gating is a wiring change, not a redesign.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Situation:
    """One situation deposited at the boundary, available for cells to pull.

    ``text`` is the human-authored turn (the thing to embed and learn). ``meta`` is
    optional provenance (model name, auth-shaped headers held for the pull, a
    completion channel for the live proxy) -- opaque to the buffer, threaded by the
    caller. The buffer stores it but never inspects ``meta``."""
    text: str
    meta: dict[str, Any] = field(default_factory=dict)


class SituationBuffer:
    """A passive, thread-safe, broadcast log of situations.

    ``deposit`` appends (external only). ``situations`` returns a snapshot list for a
    cell to scan and pull from (read-only; taking does not mutate the buffer). No
    routing, no exclusivity, no push. A cell's curiosity decides what it takes; this
    structure only holds what the boundary fed in."""

    def __init__(self) -> None:
        self._items: list[Situation] = []
        self._lock = threading.Lock()

    def deposit(self, text: str, meta: dict[str, Any] | None = None) -> int:
        """Append a situation at the boundary (a real user turn, via the proxy). The
        only write path; cells never call this. Returns the new situation's index, a
        handle the proxy uses later to attach the real response (``attach_response``)
        once the forward returns -- so a cell pulling sees a situation go from
        ``pending`` (deposited, response not yet back) to ``complete`` (response attached)."""
        with self._lock:
            self._items.append(Situation(text=text, meta=meta or {}))
            return len(self._items) - 1

    def attach_response(self, idx: int, response: str) -> None:
        """Attach the real assistant response to a deposited situation, marking it
        complete. Called ONLY by the proxy (which holds the ``SituationBuffer``), after
        the forward returns -- never by a cell (the ``BufferView`` exposes no such path,
        so the feeding boundary stays one-way: the response enters through the boundary,
        attached by the live path, never self-generated).

        ATOMIC SWAP: a cell's ``situations()`` snapshot is taken under the same lock, so it
        sees either the incomplete situation or the complete one, never a half-mutated
        ``meta`` dict. Building a new ``Situation`` and assigning ``self._items[idx]`` is
        one locked step, vs. mutating ``meta`` in place which could tear under a concurrent
        read."""
        with self._lock:
            if 0 <= idx < len(self._items):
                old = self._items[idx]
                self._items[idx] = Situation(
                    text=old.text,
                    meta={**old.meta, "response": response, "complete": True},
                )

    def situations(self) -> list[Situation]:
        """A snapshot of all deposited situations, for a cell to scan and pull from.
        Read-only: a cell taking a situation into its field does not remove it here."""
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


class BufferView:
    """The read-only face of a buffer handed to a cell.

    A cell gets a ``BufferView`` (it can scan situations), NOT the ``SituationBuffer``
    (which can deposit). This is the "the membrane cannot feed itself" guardrail in
    the type system: there is no deposit method on the view for the cell to call, so
    the boundary is enforced by what the object CAN do, not by a rule it could drop.
    """

    def __init__(self, buffer: SituationBuffer) -> None:
        self._buffer = buffer

    def situations(self) -> list[Situation]:
        return self._buffer.situations()

    def __len__(self) -> int:
        return len(self._buffer)