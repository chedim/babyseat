"""BLURP'S BODY FOLLOWING THE CURRENT (card 1, C -- operator, 2026-10-30): where the
three root skills are earned.

The body is Blurp itself, CH.SELF (#732): it sits where `self.at` says
(ch_blurp._show_body), and only Blurp's own move -- a granted look firing live --
writes `at`; the field's current (efield.current, the yellow orb) moves as people talk. On each awake exchange the body is READ: when either moved
since the last reading, the body TELLS it like a toy (toybox._tell), as field
writes in one chain of its own --
    who moved it first: `nudged` (Blurp's own move, writer self) and/or `ticked`
                        (the current's push, writer world), each its running count;
    then what it shows: `current`, the entity nearest the current (where the orb is;
                        never `at`, so the push and Blurp's move never overwrite each other),
                        and `closer`, whether the body now sits closer to the
                        current than at the last reading (cosine up).
So the learner's guesses on the body are the roots (ch_learn.kind_of): `current` is
look (its guess writes `at`: Blurp goes where it guesses the current is), `closer` after `nudged` feel itself, `closer` after `ticked` tell things
apart. Closer-or-not is the score, so there is no new threshold: nothing is
written when nothing moved, and the first reading only places the body. A reading
that shows what the last one showed is written all the same (writes.emit): the
store writes only what changed, and an unwritten repeat could never confirm a guess."""
from __future__ import annotations

import numpy as np

from . import ch as CH
from . import ch_learn as L

BODY = CH.SELF


def _unit(v) -> np.ndarray:
    a = np.asarray(v, dtype=float).ravel()
    n = float(np.linalg.norm(a))
    return a / n if n else a


def _cos(a, b) -> float:
    return float(np.dot(_unit(a), _unit(b)))


def _entity(est):
    d = (est.docs or {}).get(BODY)
    if d is not None:
        return d
    from .entities import CLASS_COMPONENT, QuantumEntity
    d = QuantumEntity(eid=BODY, cls=CLASS_COMPONENT, properties={
        "name": "self", "silent_birth": True,
        "summary": ("Blurp itself, its body following the current (card 1, C; #732): where it is "
                    "(at, its own move), who moved it (nudged = its own move, ticked = the current's "
                    "push), where the current is (current) and whether it ended closer to it (closer)")})
    est.upsert(d, embedder=None)
    return est.docs.get(BODY, d)


def read(srv, at, cur, near, gen) -> dict | None:
    """One reading of the body: `at` its place (a field vector), `cur` the current's,
    `near` the entity nearest the current, `gen` the current's generation (the field's
    current_gen: the world moved only when it did -- the vector drifts in its last bits).
    -> {by: [self|world], closer} when something moved and was told, else None."""
    at, cur = np.asarray(at, dtype=float).ravel(), np.asarray(cur, dtype=float).ravel()
    last = getattr(srv, "_ch_body_read", None)
    srv._ch_body_read = {"at": at, "cur": cur, "gen": gen}
    if last is None or last["at"].shape != at.shape or last["cur"].shape != cur.shape:
        return None                                    # the first reading places the body
    by = ([] if np.array_equal(at, last["at"]) else ["self"]) + \
         ([] if gen == last.get("gen") else ["world"])
    if not by:
        return None
    closer = _cos(at, cur) > _cos(last["at"], last["cur"])
    est = getattr(srv, "entity_store", None)
    if est is None:
        return {"by": by, "closer": closer}
    from . import writes as W
    with W.source("self", "body"):
        ent = _entity(est)                             # its birth too: in the reading's chain
        for who in by:
            cause = "nudged" if who == "self" else "ticked"
            with W.as_writer(who):                     # the count goes on across restarts
                ent.properties[cause] = int(ent.properties.get(cause) or 0) + 1
                est.upsert(ent, embedder=None)
        shows = {L.SELF_PUSH: str(near or ""), "closer": closer}
        same = {k for k, v in shows.items() if k in ent.properties and ent.properties[k] == v
                and type(ent.properties[k]) is type(v)}
        with W.as_writer(by[-1]):
            ent.properties.update(shows)
            est.upsert(ent, embedder=None)
            for k in sorted(same):                     # a reading that repeats is still a reading:
                W.emit(f"{BODY}.{k}", shows[k], shows[k], eid=BODY)   # written, so a guess of it can come true
    return {"by": by, "closer": closer}
