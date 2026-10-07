"""RELATIONS as VSA composites: (subject, relation, object) triples encoded
as bind(role_subj, key(A)) + bind(role_rel, key(rel)) + bind(role_obj,
key(B)) -- a bundle whose parts stay recoverable by unbinding.

Why VSA and not the embedder: mxbai is nearly order-blind for relations
(E25: 'A calls B' vs 'B calls A' at cos 0.975, structure-awareness 0.06);
role binding keeps the roles. The role vectors are hash-random (eids.
role_key): roles drawn from the SEMANTIC codebook are near-equal strings,
hence near-equal vectors, and cannot encode order (E25: 0.28 -> 0.58 once
the roles were random).

Triples are stored in the `relations` collection per origin: {subj, rel,
obj, source, n, first/last ts, evidence event keys, the packed composite}."""

from __future__ import annotations

import numpy as np

from . import eids as E
from . import vsa
from .efstore import h16, o8

MAX_EVIDENCE = 8


def compose(subj: str, rel: str, obj: str, dim: int = vsa.DEFAULT_DIM) -> np.ndarray:
    return vsa.weighted_majority(
        [vsa.bind(E.role_key("subj", dim), E.entity_key(subj, dim)),
         vsa.bind(E.role_key("rel", dim), E.entity_key("rel:" + rel, dim)),
         vsa.bind(E.role_key("obj", dim), E.entity_key(obj, dim))], [1.0, 1.0, 1.0])


def sim(a, b) -> float:
    """1 identical, ~0 unrelated (from the Hamming distance)."""
    return 1.0 - 2.0 * vsa.hamming(a, b)


def structure_awareness(pairs, other_pairs, encode) -> float:
    """(1 - sim(fact, reversal)) / (1 - sim(fact, unrelated)): 0 = order-
    blind, 1 = the reversal is as different as an unrelated fact."""
    sr = float(np.mean([sim(encode(*p), encode(p[2], p[1], p[0])) for p in pairs]))
    so = float(np.mean([sim(encode(*p), encode(*q)) for p, q in zip(pairs, other_pairs)]))
    return (1.0 - sr) / (1.0 - so) if so < 1 else float("nan")


def unbind_role(composite, role: str, dim: int = vsa.DEFAULT_DIM) -> np.ndarray:
    """The filler estimate of one role (XOR binding is its own inverse)."""
    return vsa.bind(composite, E.role_key(role, dim))


def cleanup(estimate, candidates: list[str], dim: int = vsa.DEFAULT_DIM,
            k: int = 5) -> list[tuple[str, float]]:
    """Nearest entity keys to a filler estimate."""
    scored = [(c, sim(estimate, E.entity_key(c, dim))) for c in candidates]
    scored.sort(key=lambda t: -t[1])
    return scored[:k]


def relation_key(origin: str, subj: str, rel: str, obj: str) -> str:
    return f"r:{o8(origin)}:{h16(subj + '|' + rel + '|' + obj)}"


def upsert(backend, origin: str, subj: str, rel: str, obj: str, source: str,
           event_key: str = "", ts: float | None = None, dim: int = vsa.DEFAULT_DIM,
           file: str | None = None) -> str:
    key = relation_key(origin, subj, rel, obj)

    def merge(cur):
        if cur is None:
            cur = {"_k": "rel", "origin": origin, "subj": subj, "rel": rel, "obj": obj,
                   "source": source, "n": 0, "first_ts": ts, "last_ts": ts,
                   "evidence": [], "vsa": vsa.pack_state(compose(subj, rel, obj, dim)),
                   "dim": dim, "superseded": False, "file": file}
        cur["n"] = int(cur.get("n", 0)) + 1
        cur["last_ts"] = ts if ts is not None else cur.get("last_ts")
        if event_key and event_key not in cur["evidence"]:
            cur["evidence"] = (cur["evidence"] + [event_key])[-MAX_EVIDENCE:]
        cur["superseded"] = False
        return cur
    backend.cas_update("relations", key, merge)
    return key


def of(backend, origin: str, eid: str, rel: str | None = None) -> list[dict]:
    """Relations where the entity is subject or object (LIVE only --
    superseded rows are history)."""
    out = []
    for side in ("subj", "obj"):
        eq = {"origin": origin, side: eid, "superseded": False}
        if rel:
            eq["rel"] = rel
        out += [d for _, d in backend.scan("relations", eq)]
    return out


def history_of(backend, origin: str, eid: str, rel: str | None = None
               ) -> list[dict]:
    """ALL the entity's relations -- live AND superseded (the queue's
    done side reads the processed rows here)."""
    out = []
    seen: set[str] = set()
    for side in ("subj", "obj"):
        eq = {"origin": origin, side: eid}
        if rel:
            eq["rel"] = rel
        for _k, d in backend.scan("relations", eq):
            if side == "subj" and d.get("subj") != eid:
                continue
            if side == "obj" and d.get("obj") != eid:
                continue
            k = str(d.get("subj")) + "|" + str(d.get("rel", "")) + "|" \
                + str(d.get("obj"))
            if k not in seen:
                seen.add(k)
                out.append(d)
    return out


# observation relations: what a tool DID to a file
TOOL_VERBS = {"edit": "edited", "write": "wrote", "read": "read", "grep": "grepped"}


def observation_triples(tool: str, outcome: str, file_eid: str | None) -> list[tuple]:
    if not file_eid:
        return []
    verb = TOOL_VERBS.get(tool)
    if verb is None:
        return []
    if outcome in ("failed", "error", "not-found"):
        verb = f"{verb}_failed"
    return [(f"tool:{tool}", verb, file_eid)]
