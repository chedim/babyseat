"""BLURP'S BODY FOLLOWING THE CURRENT (card 1, C -- Dima, 2026-10-30): where the
three root skills are earned.

The body sits where Blurp's newest firing put it (ch_blurp._show_body: its own
move, the trigger it fired); the field's current (efield.current, the yellow orb)
moves as people talk. On each awake exchange the body is READ: when either moved
since the last reading, the body TELLS it like a toy (toybox._tell), as field
writes in one chain of its own --
    who moved it first: `nudged` (Blurp's own move, writer self) and/or `ticked`
                        (the current's push, writer world), each its running count;
    then what it shows: `at`, the entity nearest the current (where the orb is),
                        and `closer`, whether the body now sits closer to the
                        current than at the last reading (cosine up).
So the learner's guesses on the body are the roots (ch_learn.kind_of): `at` is
look, `closer` after `nudged` feel itself, `closer` after `ticked` tell things
apart. Closer-or-not is the score, so there is no new threshold: nothing is
written when nothing moved, and the first reading only places the body. A reading
that shows what the last one showed is written all the same (writes.emit): the
store writes only what changed, and an unwritten repeat could never confirm a guess."""
from __future__ import annotations

import numpy as np

from . import ch_learn as L

BODY = L.BODY
NAME = L.BODY_NAME


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
        "name": NAME, "silent_birth": True,
        "summary": ("Blurp's body following the current (card 1, C): it tells who moved it "
                    "(nudged = its own move, ticked = the current's push), where the current is "
                    "(at) and whether it ended closer to it (closer)")})
    est.upsert(d, embedder=None)
    return est.docs.get(BODY, d)


def read(srv, at, cur, near) -> dict | None:
    """One reading of the body: `at` its place (a field vector), `cur` the current's,
    `near` the entity nearest the current. -> {by: [self|world], closer} when
    something moved and was told, else None."""
    at, cur = np.asarray(at, dtype=float).ravel(), np.asarray(cur, dtype=float).ravel()
    last = getattr(srv, "_ch_body_read", None)
    srv._ch_body_read = {"at": at, "cur": cur}
    if last is None or last["at"].shape != at.shape or last["cur"].shape != cur.shape:
        return None                                    # the first reading places the body
    by = ([] if np.array_equal(at, last["at"]) else ["self"]) + \
         ([] if np.array_equal(cur, last["cur"]) else ["world"])
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
        shows = {"at": str(near or ""), "closer": closer}
        same = {k for k, v in shows.items() if k in ent.properties and ent.properties[k] == v
                and type(ent.properties[k]) is type(v)}
        with W.as_writer(by[-1]):
            ent.properties.update(shows)
            est.upsert(ent, embedder=None)
            for k in sorted(same):                     # a reading that repeats is still a reading:
                W.emit(f"{BODY}.{k}", shows[k], shows[k], eid=BODY)   # written, so a guess of it can come true
    return {"by": by, "closer": closer}
