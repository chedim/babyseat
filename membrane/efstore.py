"""ENTITY-FIELD STORAGE: one small backend contract, two homes.

  MemBackend        RAM dicts + brute-force vector top-k: tests, lab paths,
                    experiments (the isolation guard -- a lab membrane never
                    joins the shared substrate).
  CouchbaseBackend  the real seat's home (membrane scope; stage 5).

Collections (both homes): events, sentences, terms, entity_states,
entity_deltas, relations, claims, annotations, origin_stats.

Vectors: fields an ANN index reads stay float arrays; everything else is
packed float16 ('f16:' + base64) -- ~2.7 KB instead of 10-20 KB of JSON."""

from __future__ import annotations

import base64
import hashlib
import time
from typing import Callable, Iterator

import numpy as np

COLLECTIONS = ("events", "sentences", "terms", "entity_states", "entity_deltas",
               "relations", "claims", "annotations", "origin_stats")


def o8(origin: str) -> str:
    """The origin's key segment (no '@'/'%' in keys)."""
    return hashlib.sha1((origin or "").encode()).hexdigest()[:8]


def h16(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def pack_f16(v) -> str:
    a = np.asarray(v, dtype=np.float16)
    return "f16:" + base64.b64encode(a.tobytes()).decode()


def unpack_f16(s) -> np.ndarray:
    if isinstance(s, str) and s.startswith("f16:"):
        return np.frombuffer(base64.b64decode(s[4:]), dtype=np.float16).astype(np.float64)
    return np.asarray(s, dtype=np.float64)


class MemBackend:
    """The RAM home. Same contract the Couchbase backend implements."""

    def __init__(self):
        self.colls: dict[str, dict[str, dict]] = {c: {} for c in COLLECTIONS}
        self._leases: dict[str, tuple[str, float]] = {}

    def _c(self, coll: str) -> dict:
        return self.colls.setdefault(coll, {})

    def get(self, coll: str, key: str) -> dict | None:
        d = self._c(coll).get(key)
        return dict(d) if d is not None else None

    def get_multi(self, coll: str, keys) -> dict[str, dict]:
        c = self._c(coll)
        return {k: dict(c[k]) for k in keys if k in c}

    def upsert(self, coll: str, key: str, doc: dict) -> None:
        self._c(coll)[key] = dict(doc)

    def upsert_multi(self, coll: str, docs: dict[str, dict]) -> None:
        c = self._c(coll)
        for k, d in docs.items():
            c[k] = dict(d)

    def insert_if_absent(self, coll: str, key: str, doc: dict) -> bool:
        c = self._c(coll)
        if key in c:
            return False
        c[key] = dict(doc)
        return True

    def delete(self, coll: str, key: str) -> None:
        self._c(coll).pop(key, None)

    def cas_update(self, coll: str, key: str,
                   fn: Callable[[dict | None], dict | None]) -> dict | None:
        c = self._c(coll)
        new = fn(dict(c[key]) if key in c else None)
        if new is None:
            c.pop(key, None)
        else:
            c[key] = dict(new)
        return new

    def scan(self, coll: str, where: Callable[[dict], bool] | None = None,
             order: Callable[[dict], object] | None = None,
             limit: int | None = None) -> Iterator[tuple[str, dict]]:
        items = [(k, d) for k, d in self._c(coll).items() if where is None or where(d)]
        if order is not None:
            items.sort(key=lambda kd: order(kd[1]))
        for i, (k, d) in enumerate(items):
            if limit is not None and i >= limit:
                break
            yield k, dict(d)

    def vector_top_k(self, coll: str, field: str, vec, k: int,
                     where: Callable[[dict], bool] | None = None
                     ) -> list[tuple[str, float]]:
        q = np.asarray(vec, dtype=float)
        q = q / (np.linalg.norm(q) + 1e-12)
        keys, rows = [], []
        for key, d in self._c(coll).items():
            v = d.get(field)
            if v is None or (where is not None and not where(d)):
                continue
            keys.append(key)
            rows.append(unpack_f16(v))
        if not rows:
            return []
        M = np.array(rows)
        M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)
        s = M @ q
        idx = np.argsort(-s)[:k]
        return [(keys[i], float(s[i])) for i in idx]

    def lease(self, name: str, holder: str, ttl_s: float) -> bool:
        now = time.time()
        cur = self._leases.get(name)
        if cur and cur[0] != holder and cur[1] > now:
            return False
        self._leases[name] = (holder, now + ttl_s)
        return True

    def close(self) -> None:
        pass
