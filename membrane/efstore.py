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


def pack_f32(v) -> str:
    """Running sums (mean, residual sum) need float32: float16 keeps ~3
    significant digits and a sum of thousands of unit vectors drifts."""
    a = np.asarray(v, dtype=np.float32)
    return "f32:" + base64.b64encode(a.tobytes()).decode()


def unpack_f16(s) -> np.ndarray:
    """Any packed vector ('f16:' / 'f32:') or a plain float list."""
    if isinstance(s, str) and s.startswith("f16:"):
        return np.frombuffer(base64.b64decode(s[4:]), dtype=np.float16).astype(np.float64)
    if isinstance(s, str) and s.startswith("f32:"):
        return np.frombuffer(base64.b64decode(s[4:]), dtype=np.float32).astype(np.float64)
    return np.asarray(s, dtype=np.float64)


_RANGE = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def matches(d: dict, eq: dict | None) -> bool:
    """eq: {field: value} (equal), {field: [values]} (IN), {field: {"gt": v}}
    (a range; gt / gte / lt / lte, combinable)."""
    for f, want in (eq or {}).items():
        got = d.get(f)
        if isinstance(want, dict):
            if got is None or not all(
                    {"gt": got > v, "gte": got >= v, "lt": got < v, "lte": got <= v}[op]
                    for op, v in want.items()):
                return False
        elif isinstance(want, (list, tuple, set, frozenset)):
            if got not in want:
                return False
        elif got != want:
            return False
    return True


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

    def scan(self, coll: str, eq: dict | None = None, order: tuple = (),
             limit: int | None = None) -> Iterator[tuple[str, dict]]:
        """Rows whose fields equal `eq` (a list value means IN), ordered by
        the `order` fields ascending -- the shape SQL++ can express."""
        items = [(k, d) for k, d in self._c(coll).items() if matches(d, eq)]
        if order:
            items.sort(key=lambda kd: tuple(kd[1].get(f) if kd[1].get(f) is not None
                                            else 0 for f in order))
        for i, (k, d) in enumerate(items):
            if limit is not None and i >= limit:
                break
            yield k, dict(d)

    def vector_top_k(self, coll: str, field: str, vec, k: int,
                     eq: dict | None = None) -> list[tuple[str, float]]:
        q = np.asarray(vec, dtype=float)
        q = q / (np.linalg.norm(q) + 1e-12)
        keys, rows = [], []
        for key, d in self._c(coll).items():
            v = d.get(field)
            if v is None or not matches(d, eq):
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


# ---- the Couchbase home ----------------------------------------------------------
SCOPE = "membrane"
# composite secondary indexes: the scans the field actually runs
GSI = (("events", ("origin", "status", "seq")),
       ("events", ("origin", "ts")),                  # the --no-context reconstruction
       ("events", ("origin", "n")),                   # '#N' -> the event
       ("entity_deltas", ("origin", "eid", "seq", "sidx")),
       ("entity_deltas", ("origin", "seq")),          # the state diff since a watermark
       ("entity_deltas", ("origin", "sid")),          # the gate: sentence -> its events
       ("entity_states", ("origin", "tier", "kind")),
       ("entity_states", ("eid", "tier")),            # merged reads across seats
       ("entity_deltas", ("eid", "origin", "seq")))
CHUNK = 500


class _Busy(Exception):
    pass


def scan_sql(bucket: str, scope: str, coll: str, eq: dict | None,
             order: tuple = (), limit: int | None = None) -> tuple[str, dict]:
    """The SQL++ for one scan (pure: tested without a cluster)."""
    where, params = [], {}
    for i, (f, want) in enumerate((eq or {}).items()):
        p = f"p{i}"
        if isinstance(want, dict):
            for j, (op, v) in enumerate(want.items()):
                where.append(f"t.`{f}` {_RANGE[op]} ${p}_{j}")
                params[f"{p}_{j}"] = v
        elif isinstance(want, (list, tuple, set, frozenset)):
            where.append(f"t.`{f}` IN ${p}")
            params[p] = list(want)
        else:
            where.append(f"t.`{f}` = ${p}")
            params[p] = want
    sql = f"SELECT META(t).id AS k, t AS d FROM `{bucket}`.`{scope}`.`{coll}` t"
    if where:
        sql += " WHERE " + " AND ".join(where)
    if order:
        sql += " ORDER BY " + ", ".join(f"t.`{f}`" for f in order)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def knn_filter(eq: dict | None) -> dict | None:
    """A search-query pre-filter for the vector index (keyword fields)."""
    parts = []
    for f, want in (eq or {}).items():
        vals = list(want) if isinstance(want, (list, tuple, set, frozenset)) else [want]
        terms = [{"term": str(v), "field": f} for v in vals]
        parts.append(terms[0] if len(terms) == 1 else {"disjuncts": terms})
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else {"conjuncts": parts}


class CouchbaseBackend:
    """The real seat's home: collections in the `membrane` scope, KV for
    point reads/writes, SQL++ (request-plus: a scan sees the writes just
    made) for scans, the search service's vector index for top-k with a
    brute-force fallback, CAS for read-modify-write and the applier lease."""

    def __init__(self, cluster, bucket: str, scope: str = SCOPE, ensure: bool = True):
        self.cluster = cluster
        self.bucket_name = bucket
        self.scope_name = scope
        self._scope = cluster.bucket(bucket).scope(scope)
        self._colls: dict = {}
        self._vec_ready: dict = {}
        self.created = self.ensure_infra() if ensure else []

    # ---- bootstrap -----------------------------------------------------------------
    def ensure_infra(self) -> list[str]:
        """Idempotent: scope, collections, primary + composite indexes."""
        created = []
        mgr = self.cluster.bucket(self.bucket_name).collections()
        try:
            mgr.create_scope(self.scope_name)
            created.append(f"scope {self.scope_name}")
        except Exception:
            pass
        for c in COLLECTIONS:
            try:
                mgr.create_collection(self.scope_name, c)
                created.append(f"collection {c}")
            except Exception:
                pass
        for c in COLLECTIONS:
            try:
                self._query(f"CREATE PRIMARY INDEX IF NOT EXISTS ON "
                            f"`{self.bucket_name}`.`{self.scope_name}`.`{c}`")
            except Exception:
                pass
        for c, fields in GSI:
            name = f"ef_{c}_{'_'.join(fields)}"
            try:
                self._query(f"CREATE INDEX `{name}` IF NOT EXISTS ON "
                            f"`{self.bucket_name}`.`{self.scope_name}`.`{c}`"
                            f"({', '.join(f'`{f}`' for f in fields)})")
            except Exception:
                pass
        return created

    def _c(self, coll: str):
        h = self._colls.get(coll)
        if h is None:
            h = self._colls[coll] = self._scope.collection(coll)
        return h

    def _query(self, sql: str, params: dict | None = None):
        from couchbase.n1ql import QueryScanConsistency
        from couchbase.options import QueryOptions
        return self.cluster.query(sql, QueryOptions(
            named_parameters=params or {},
            scan_consistency=QueryScanConsistency.REQUEST_PLUS))

    # ---- KV ------------------------------------------------------------------------
    def get(self, coll: str, key: str) -> dict | None:
        from couchbase.exceptions import DocumentNotFoundException
        try:
            return self._c(coll).get(key).content_as[dict]
        except DocumentNotFoundException:
            return None

    def get_multi(self, coll: str, keys) -> dict[str, dict]:
        keys = list(dict.fromkeys(keys))
        out = {}
        for lo in range(0, len(keys), CHUNK):
            res = self._c(coll).get_multi(keys[lo:lo + CHUNK])
            for k, r in (res.results or {}).items():
                try:
                    out[k] = r.content_as[dict]
                except Exception:
                    pass
        return out

    def upsert(self, coll: str, key: str, doc: dict) -> None:
        self._c(coll).upsert(key, doc)

    def upsert_multi(self, coll: str, docs: dict[str, dict]) -> None:
        items = list(docs.items())
        for lo in range(0, len(items), CHUNK):
            self._c(coll).upsert_multi(dict(items[lo:lo + CHUNK]))

    def insert_if_absent(self, coll: str, key: str, doc: dict) -> bool:
        from couchbase.exceptions import DocumentExistsException
        try:
            self._c(coll).insert(key, doc)
            return True
        except DocumentExistsException:
            return False

    def delete(self, coll: str, key: str) -> None:
        from couchbase.exceptions import DocumentNotFoundException
        try:
            self._c(coll).remove(key)
        except DocumentNotFoundException:
            pass

    def cas_update(self, coll: str, key: str, fn, retries: int = 8) -> dict | None:
        from couchbase.exceptions import (CasMismatchException,
                                          DocumentExistsException,
                                          DocumentNotFoundException)
        from couchbase.options import ReplaceOptions
        c = self._c(coll)
        for _ in range(retries):
            try:
                r = c.get(key)
                cur, cas = r.content_as[dict], r.cas
            except DocumentNotFoundException:
                cur, cas = None, None
            new = fn(dict(cur) if cur is not None else None)
            try:
                if new is None:
                    if cur is not None:
                        c.remove(key)
                elif cur is None:
                    c.insert(key, new)
                else:
                    c.replace(key, new, ReplaceOptions(cas=cas))
                return new
            except (CasMismatchException, DocumentExistsException, DocumentNotFoundException):
                continue                                # lost the race: re-read
        raise RuntimeError(f"cas_update({coll}, {key}): too much contention")

    # ---- scans + vectors ---------------------------------------------------------------
    def scan(self, coll: str, eq: dict | None = None, order: tuple = (),
             limit: int | None = None) -> Iterator[tuple[str, dict]]:
        sql, params = scan_sql(self.bucket_name, self.scope_name, coll, eq, order, limit)
        for row in self._query(sql, params):
            yield row["k"], row["d"]

    def vector_top_k(self, coll: str, field: str, vec, k: int,
                     eq: dict | None = None) -> list[tuple[str, float]]:
        import os
        if os.environ.get("MEMBRANE_EF_ANN", "1") != "0":
            hits = self._ann(coll, field, vec, k, eq)
            if hits is not None:
                return hits
        q = np.asarray(vec, dtype=float)
        q = q / (np.linalg.norm(q) + 1e-12)
        scored = []
        for key, d in self.scan(coll, eq):
            v = d.get(field)
            if v is None:
                continue
            v = unpack_f16(v)
            scored.append((key, float(v @ q / (np.linalg.norm(v) + 1e-12))))
        scored.sort(key=lambda t: -t[1])
        return scored[:k]

    def _ann(self, coll, field, vec, k, eq):
        from . import ann
        name = f"ef_{self.bucket_name}_{coll}_{field}".replace("-", "_")
        if name not in self._vec_ready:
            self._vec_ready[name] = ann.ensure_vector_index(
                self._scope, name, self.bucket_name, self.scope_name, coll,
                field, len(vec), tuple((eq or {}).keys()))
        if not self._vec_ready[name]:
            return None
        return ann.ann_search(name, list(vec), k=k, field=field, filter=knn_filter(eq))

    def lease(self, name: str, holder: str, ttl_s: float) -> bool:
        now = time.time()

        def take(cur):
            if cur and cur.get("holder") != holder and cur.get("until", 0) > now:
                raise _Busy()
            return {"_k": "lease", "holder": holder, "until": now + ttl_s}
        try:
            self.cas_update("origin_stats", f"lease:{name}", take)
            return True
        except _Busy:
            return False

    def close(self) -> None:
        pass


def open_backend(field_path: str = ""):
    """RAM for lab paths / MEMBRANE_EFIELD_STORE=mem (the isolation guard);
    Couchbase for real seats; RAM again if the cluster is unreachable
    (degrade, never raise at boot)."""
    import os
    import sys
    from .substrate import _is_lab_field_path, cb_open
    if os.environ.get("MEMBRANE_EFIELD_STORE", "auto") == "mem" or (
            field_path and _is_lab_field_path(field_path)):
        return MemBackend()
    try:
        cluster, bucket = cb_open()
        be = CouchbaseBackend(cluster, bucket)
        if be.created:
            print(f"[efield] bootstrapped: {', '.join(be.created)}", file=sys.stderr)
        return be
    except Exception as e:
        print(f"[efield] couchbase unavailable ({e}); RAM backend", file=sys.stderr)
        return MemBackend()
