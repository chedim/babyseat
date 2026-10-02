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
import os
import sys
import threading
import time
from pathlib import Path
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
        # THE SCAN LOCK (the 'dictionary changed size during iteration'
        # flood: the capture worker's embed/apply scans + the chat
        # thread's write_events upserts share one RAM dict -- the
        # iteration must be atomic against upserts). The Couchbase
        # backend inherits nothing here: its scans run server-side
        # (N1QL), the RAM race does not exist there.
        self._ram_lock = threading.RLock()

    def _c(self, coll: str) -> dict:
        with self._ram_lock:
            return self.colls.setdefault(coll, {})

    def get(self, coll: str, key: str) -> dict | None:
        with self._ram_lock:
            d = self._c(coll).get(key)
            return dict(d) if d is not None else None

    def get_multi(self, coll: str, keys) -> dict[str, dict]:
        with self._ram_lock:
            c = self._c(coll)
            return {k: dict(c[k]) for k in keys if k in c}

    def upsert(self, coll: str, key: str, doc: dict) -> None:
        with self._ram_lock:
            self._c(coll)[key] = dict(doc)

    def upsert_multi(self, coll: str, docs: dict[str, dict]) -> None:
        with self._ram_lock:
            c = self._c(coll)
            for k, d in docs.items():
                c[k] = dict(d)

    def insert_if_absent(self, coll: str, key: str, doc: dict) -> bool:
        with self._ram_lock:
            c = self._c(coll)
            if key in c:
                return False
            c[key] = dict(doc)
            return True

    def delete(self, coll: str, key: str) -> None:
        with self._ram_lock:
            self._c(coll).pop(key, None)

    def cas_update(self, coll: str, key: str,
                   fn: Callable[[dict | None], dict | None]) -> dict | None:
        with self._ram_lock:
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
        the `order` fields ascending -- the shape SQL++ can express.
        THE SNAPSHOT (the 'Set changed size during iteration' flood):
        the matches+sort run on a COPY taken under the RAM lock --
        another thread's upsert mid-scan never tears the iteration."""
        with self._ram_lock:
            items = [(k, dict(d)) for k, d in self._c(coll).items()
                     if matches(d, eq)]
        for f in reversed(order):                        # stable multi-key sort
            fld = f[0] if isinstance(f, tuple) else f
            rev = isinstance(f, tuple) and not str(f[1]).upper().startswith("ASC")
            items.sort(key=lambda kd, fld=fld: 0 if kd[1].get(fld) is None
                       else kd[1].get(fld), reverse=rev)
        for i, (k, d) in enumerate(items):
            if limit is not None and i >= limit:
                break
            yield k, d

    def count(self, coll: str, eq: dict | None = None, group: tuple = ()) -> list[dict]:
        """Grouped counts (rows of 'n' + the group fields); RAM aggregation
        over the same matches() path the scan uses."""
        if not group:
            return {"n": sum(1 for _ in self.scan(coll, eq))}
        counts: dict[tuple, int] = {}
        for _, d in self.scan(coll, eq):
            key = tuple(d.get(f) if d.get(f) is not None else "?" for f in group)
            counts[key] = counts.get(key, 0) + 1
        return [dict(zip(group, ks), n=n) for ks, n in counts.items()]

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
       ("events", ("origin", "stream", "status")),  # grouped counts (vitals)
       ("entity_states", ("origin", "tier", "accr")),  # top-m by mass
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
        parts = []
        for f in order:
            if isinstance(f, tuple):
                parts.append(f"t.`{f[0]}` {f[1]}")
            else:
                parts.append(f"t.`{f}`")
        sql += " ORDER BY " + ", ".join(parts)
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def count_sql(bucket: str, scope: str, coll: str, eq: dict | None = None,
              group: tuple = ()) -> tuple[str, dict]:
    """SQL++ for server-side counts over one collection (pure: tested
    without a cluster): rows of `n` -- plus the group fields when
    grouped -- served from the composite GSI, never a client scan."""
    where, params = [], {}
    for i, (f, want) in enumerate((eq or {}).items()):
        p = f"p{i}"
        where.append(f"t.`{f}` = ${p}")
        params[p] = want
    gsel = ", ".join(f"t.`{f}` AS `{f}`" for f in group)
    gby = " GROUP BY " + ", ".join(f"t.`{f}`" for f in group) if group else ""
    sql = f"SELECT {gsel + ', ' if gsel else ''}COUNT(*) AS `n` FROM `{bucket}`.`{scope}`.`{coll}` t"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + gby, params


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
                self._run(f"CREATE PRIMARY INDEX IF NOT EXISTS ON "
                            f"`{self.bucket_name}`.`{self.scope_name}`.`{c}`")
            except Exception:
                pass
        for c, fields in GSI:
            name = f"ef_{c}_{'_'.join(fields)}"
            try:
                self._run(f"CREATE INDEX `{name}` IF NOT EXISTS ON "
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

    def _run(self, sql: str) -> list:
        """Execute a statement now (the SDK's query result is lazy: an
        un-iterated CREATE INDEX never runs)."""
        return list(self._query(sql))

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

    def count(self, coll: str, eq: dict | None = None, group: tuple = ()) -> list[dict]:
        """Server-side counts (SQL++ COUNT(*) with an indexed GROUP BY):
        no doc ever crosses to the client for a number."""
        sql, params = count_sql(self.bucket_name, self.scope_name, coll, eq, group)
        rows = list(self._query(sql, params))
        if not group:
            return {"n": rows[0]["n"]} if rows else {"n": 0}
        return [dict(r) for r in rows]

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
        # a scope-level search index answers only to its full name
        return ann.ann_search(f"{self.bucket_name}.{self.scope_name}.{name}", list(vec), k=k,
                              field=field, filter=knn_filter(eq), timeout=5.0)

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


# ---- the FILE home (stage 4, the seat-first flip) ---------------------------------
FIELD_DIRNAME = ".membrane/field"

def _field_dirname_for(field_path) -> str:
    """THE NESTED-DIR GUARD (the '[efield] sentences snapshot failed'
    exhibit: '/…/membrane/.membrane/.membrane/field/sentences.json' --
    the join doubled .membrane when the field path's parent WAS the
    .membrane dir (a field path of .membrane/field.json). A data dir
    already under .membrane joins just 'field'; the canonical seat
    (rag-field.json in the cwd) keeps '.membrane/field'."""
    try:
        parent = Path(field_path).resolve().parent \
            if field_path else Path.cwd()
        if parent.name == ".membrane":
            return "field"
    except Exception:
        pass
    return FIELD_DIRNAME


class FileBackend(MemBackend):
    """THE SEAT-FIRST HOME (stage 4): the entity field lives under the
    seat's OWN folder -- ``<seat>/.membrane/field/`` -- and survives
    every restart without couchbase. RAM speed (it IS a MemBackend --
    scans and vectors are inherited exactly) with persistence:

      events.jsonl  the append-only log. EVERY write appends; replay is
                    LAST LINE WINS (a status change -- pending ->
                    applied -> served -- appends too). Compacted to the
                    live rows on flush when the log runs > 2x live.
      <coll>.json   every OTHER collection: a debounced full snapshot,
                    written atomically (tmp + rename).

    A crash loses at most one flush window of snapshot updates -- no
    event is ever lost (appended the moment it was written). The couchbase
    field state write-throughs are RETIRED by default (the global tier
    holds only published entities now); MEMBRANE_FIELD_CLOUD=1 opts back
    into the cloud field for a seat that wants merged reads."""

    DEBOUNCE_S = 2.0

    def __init__(self, path):
        import json
        self._json = json
        super().__init__()
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events_log = self.dir / "events.jsonl"
        self._dirty: set = set()
        # THE DIRTY-SET LOCK (the 'Set changed size during iteration'
        # flood, class 2): _touch runs on the write threads; flush runs
        # on the worker/timer threads -- both iterate/mutate _dirty.
        # Same lesson MemBackend's scans learned: a shared set is only
        # safe under a lock.
        self._flush_lock = threading.RLock()
        self._last_flush = time.time()
        self._load()

    def _load(self) -> None:
        """Replay the log (torn finals skipped -- a kill mid-append) and
        load the snapshots."""
        if self._events_log.exists():
            with self._events_log.open() as fh:
                for line in fh:
                    try:
                        row = self._json.loads(line)
                    except Exception:
                        continue
                    k, d = row.get("k"), row.get("d")
                    if k and isinstance(d, dict):
                        self.colls["events"][k] = d
        colls = self.colls
        for coll in COLLECTIONS:
            if coll == "events":
                continue
            f = self.dir / f"{coll}.json"
            if f.exists():
                try:
                    colls[coll] = self._json.loads(f.read_text())
                except Exception:
                    pass

    def _append(self, key, doc) -> None:
        try:
            with self._events_log.open("a") as fh:
                fh.write(self._json.dumps({"k": key, "d": doc}) + "\n")
        except Exception as e:
            print(f"[efield] event append failed ({e}); RAM carries",
                  file=sys.stderr)

    def _touch(self, coll: str) -> None:
        with self._flush_lock:
            self._dirty.add(coll)
            due = time.time() - self._last_flush >= self.DEBOUNCE_S
        if due:
            self.flush()

    def upsert(self, coll: str, key: str, doc: dict) -> None:
        super().upsert(coll, key, doc)
        if coll == "events":
            self._append(key, doc)
        self._touch(coll)

    def upsert_multi(self, coll: str, docs: dict) -> None:
        super().upsert_multi(coll, docs)
        if coll == "events":
            for k, d in docs.items():
                self._append(k, d)
        self._touch(coll)

    def insert_if_absent(self, coll: str, key: str, doc: dict) -> bool:
        added = super().insert_if_absent(coll, key, doc)
        if added:
            if coll == "events":
                self._append(key, doc)
            self._touch(coll)
        return added

    def cas_update(self, coll: str, key, fn):
        new = super().cas_update(coll, key, fn)
        self._touch(coll)
        return new

    def delete(self, coll: str, key: str) -> None:
        super().delete(coll, key)
        self._touch(coll)

    def flush(self) -> None:
        """Write the dirty collections atomically; the events log is
        compacted (not snapshotted -- it IS the event store)."""
        # THE SNAPSHOT-THEN-WRITE: the dirty set is taken + cleared UNDER
        # the flush lock (a concurrent _touch re-dirties for the next
        # round); the serialization itself reads stable dicts
        # (per-collection data locks live in MemBackend now).
        with self._flush_lock:
            todo = list(self._dirty)
            self._dirty.clear()
            self._last_flush = time.time()
        self.dir.mkdir(parents=True, exist_ok=True)
        for coll in todo:
            if coll == "events":
                self._compact_events()
                continue
            f = self.dir / f"{coll}.json"
            tmp = f.with_suffix(".json.tmp")
            try:
                with self._ram_lock:
                    data = self._json.dumps(self.colls.get(coll, {}))
                tmp.write_text(data)
                tmp.replace(f)
            except Exception as e:
                print(f"[efield] {coll} snapshot failed ({e}); RAM carries",
                      file=sys.stderr)
        return

    def _compact_events(self) -> None:
        """The log's GC: when the appended writes out-carry the live
        events (statuses churn ~2-3 lines per event), rewrite one row
        per live key -- atomic tmp + replace, same replay format.
        THE ITERATION GUARD: the events dict is snapshot under the RAM
        lock (an upsert mid-compaction used to tear the walk too)."""
        try:
            n_lines = sum(1 for _ in self._events_log.open())
            with self._ram_lock:
                n_live = len(self.colls["events"])
                if n_lines <= 2 * n_live + 256:
                    return
                rows = [(k, dict(d)) for k, d in
                        self.colls["events"].items()]
            tmp = self._events_log.with_suffix(".jsonl.tmp")
            with tmp.open("w") as fh:
                for k, d in rows:
                    fh.write(self._json.dumps({"k": k, "d": d}) + "\n")
            tmp.replace(self._events_log)
        except Exception as e:
            print(f"[efield] events compaction failed ({e}); log carries",
                  file=sys.stderr)

    def close(self) -> None:
        self.flush()


def open_backend(field_path: str = ""):
    """THE SEAT-FIRST LADDER (the operator's stage 4): RAM for lab paths /
    MEMBRANE_EFIELD_STORE=mem (the isolation guard); the FILE home under
    the seat's own folder by DEFAULT (the field survives restarts, and
    the raw per-origin state write-throughs are retired); the couchbase
    field only when MEMBRANE_FIELD_CLOUD=1 -- and a down cluster degrades
    to FILE, never to RAM (RAM forgets on restart)."""
    import os
    import sys
    from .substrate import _is_lab_field_path
    if os.environ.get("MEMBRANE_EFIELD_STORE", "auto") == "mem" or (
            field_path and _is_lab_field_path(field_path)):
        return MemBackend()
    if os.environ.get("MEMBRANE_FIELD_CLOUD", "0") == "1":
        try:
            from .substrate import cb_open
            cluster, bucket = cb_open()
            be = CouchbaseBackend(cluster, bucket)
            if be.created:
                print(f"[efield] bootstrapped: {', '.join(be.created)}",
                      file=sys.stderr)
            return be
        except Exception as e:
            print(f"[efield] couchbase unavailable ({e}); file backend",
                  file=sys.stderr)
    base = Path(field_path).resolve().parent if field_path else Path.cwd()
    try:
        return FileBackend(base / _field_dirname_for(field_path))
    except Exception as e:
        print(f"[efield] file backend unavailable ({e}); RAM backend",
              file=sys.stderr)
        return MemBackend()

def probe_local_couchbase() -> bool:
    """Cheap one-shot probe: does a local Couchbase answer at the default
    (or env-provided) endpoint with default (or env-provided) creds?

    Plain HTTP GET /pools with Basic auth -- no SDK import, ~1s cap. True
    only when the server responds 200; auth failures, DNS failures,
    timeouts, and every other refusal are all 'no' (a memory backend, as
    always, the safe fallback). Env vars override defaults."""
    import base64
    import urllib.request
    conn = os.environ.get("COUCHBASE_CONN_STRING", "couchbase://127.0.0.1")
    user = os.environ.get("COUCHBASE_USER", "Administrator")
    pw = os.environ.get("COUCHBASE_PASSWORD", "password")
    host = conn.replace("couchbase://", "").replace("http://", "") \
               .split("/")[0].split(":")[0]
    try:
        req = urllib.request.Request(f"http://{host}:8091/pools")
        req.add_header("Authorization", "Basic " + base64.b64encode(
            f"{user}:{pw}".encode()).decode())
        with urllib.request.urlopen(req, timeout=2.0) as r:
            return r.status == 200
    except Exception:
        return False
