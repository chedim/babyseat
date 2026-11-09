"""THE COUCHBASE LITE HOME (2026-10-10, the operator: "switch to couchbase
lite -- if running on android"). The seat's phone ran its field as RAM
dicts with JSONL journals: 2.7 GB resident on an 8 GB phone, a 1.35 GB
journal replay at every boot, the sentences' 187k x 1024 vectors held
twice (the docs + a float16 matrix). Measured on the same shapes
(scratch bench, 187k sentences): boot 2.4 s -> 5 ms, vector top-120
40 ms -> 6-7 ms at recall 0.99, RAM 2.1 GB -> ~0.5 GB.

THE SPLIT (the semantics stay the RAM home's, by construction):

  sentences      COLD -- not in RAM. Point reads/writes go to CBL; the
                 vectors are a LAZY CBL vector index (a save stays ~0.2 ms;
                 a background catch-up indexes new rows in batches -- the
                 RAM home's stale-ok, the same promise). Scans (the move
                 tool only) iterate CBL.
  everything     HOT -- the RAM home exactly (MemBackend: its scans, its
  else           indexes, its vector cache for entity_states/terms); CBL is
                 only their PERSISTENCE in place of the JSONL journals: every
                 write saves the doc, the boot loads the LIVE rows (no history
                 replay), in their first-written order.

Stored body: {"o": first-written ordinal, "d": the doc}. The ordinal keeps
the RAM home's dict order across restarts (scans answer in it).

THE MIGRATION: a fresh database next to an old journal dir imports it once
(streamed, last-line-wins, tombstones purge); the journals stay as they
were -- a backup, never written again."""
from __future__ import annotations

import base64
import fcntl
import itertools
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import cblite as CB


class StoreBusy(RuntimeError):
    """The store's writer lock is held by another membrane process
    (issue #26): open_backend fails fast -- a replay beside a live seat
    must never silently fall back to the stale file journals."""
from .efstore import COLLECTIONS, WARM_DIR, MemBackend, matches

COLD = ("sentences",)
VEC = {"sentences": ("vec", "sent_vec")}            # coll -> (field, index name)
DIM = 1024
META = "meta"
# THE PROBES (#429, the recall-gap exhibit): the sentence index is IVF
# (sqrt(n) centroids, SQ8); a query left at the index's default probes read
# 8 of its clusters, and on a copy of a live field (120k sentences) its
# top-100 held 71% of the exact top-100 -- a true nearest sentence missed the
# top 2000. Probes ride each query (APPROX_VECTOR_DISTANCE's 4th argument),
# so no index is rebuilt. Measured there: 16 -> 0.86, 32 -> 0.97 (every
# probe question's exact #1 found), 64 -> 0.987 at 1.7x the time.
NPROBES = 32
CACHE_N = 16384                                    # decoded cold docs kept (~50 MB: the gate and the banner re-read their sids)


def _finite(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    return o


def dumps(body) -> str:
    """JSON CBL accepts, always: no NaN/Infinity (-> null), strangers wrap
    to strings (the journal's json-safe lesson: one poison row must never
    kill a write)."""
    try:
        return json.dumps(body, allow_nan=False)
    except ValueError:
        return dumps(_finite(body))
    except TypeError:
        from .entities import json_safe
        return json.dumps(_finite(json_safe(body)), allow_nan=False)


def unpack_vec(s: str):
    """The stored vector -> float32, unit length ('f16:'/'f32:' packed, or a
    JSON list)."""
    if not isinstance(s, str) or not s:
        return None
    if s.startswith("f16:"):
        a = np.frombuffer(base64.b64decode(s[4:]), np.float16).astype(np.float32)
    elif s.startswith("f32:"):
        a = np.frombuffer(base64.b64decode(s[4:]), np.float32).astype(np.float32)
    else:
        try:
            a = np.asarray(json.loads(s), np.float32)
        except Exception:
            return None
    if a.size != DIM:
        return None
    n = float(np.linalg.norm(a))
    return a / n if n > 0 else None


class CBLiteBackend(MemBackend):
    KIND = "cbl"
    LABEL = "couchbase lite"
    CATCHUP_BATCH = 500
    CATCHUP_IDLE_S = 30.0
    # THE WRITE-BEHIND (2026-10-10, the first live rounds: the banner's field
    # flush paid a synchronous CBL save per dirty entity state, inside the
    # RAM lock, every round): the hot rows' RAM is the truth; their saves
    # coalesce (the same state rewritten round after round = one save) and
    # land in one transaction per WRITE_BEHIND_S. close/flush drain it.
    WRITE_BEHIND_S = 0.5

    def __init__(self, path, journal_dir=None, lib_dir=None, readonly=False):
        super().__init__()
        self.path = Path(path)
        self.readonly = readonly
        # THE WRITER LOCK (issue #26): one writer process at a time. SQLite
        # storage lets a second open succeed and only blow up later, mid-
        # replay ("save: database busy/locked", the backfill's partial
        # rebuild) -- so a sidecar flock is taken here, where EVERY tool
        # (the serve, the backfill) constructs the backend. A READ-ONLY open
        # (issue #127: review beside a live serve) skips the flock entirely
        # and reads a snapshot: no writer, no vector catch-up, no migration
        # or tail replay -- nothing this handle can write.
        if not readonly:
            self.path.parent.mkdir(parents=True, exist_ok=True)   # a brand-new seat has no .membrane yet (#971)
            lk = self.path.parent / f".{self.path.name}.writer.lock"
            self._lock_fd = os.open(str(lk), os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(self._lock_fd)
                self._lock_fd = None
                raise StoreBusy(f"the store is busy: {lk} is held by another "
                                f"membrane process (the live serve?)") from None
        else:
            self._lock_fd = None
        self.lib, self.db = self._open_db(lib_dir)
        self._ord: dict[str, dict[str, int]] = {c: {} for c in COLLECTIONS}
        self._next: dict[str, int] = {c: 0 for c in COLLECTIONS}
        self._cache: dict[str, dict] = {}
        self._wake = threading.Event()
        self._stop = False
        self._idx_rows = 0
        self._idx_last = 0.0
        self._pending: dict[tuple[str, str], tuple] = {}
        self._pend_lock = threading.Lock()
        self._wflush = threading.Event()
        self._retry_at = 0
        self._fails = 0
        self._writer = None
        if readonly:
            # a snapshot: load the hot rows (the collections already exist --
            # the live seat created them) and stop. No index creation (a
            # write), no migration, no tail replay, no threads.
            self._worker = None
            self._load_hot()
            return
        self._ensure_indexes()
        if journal_dir is not None and self._meta("migrated") is None:
            self.migrate_from(Path(journal_dir))
        self._ensure_vector_indexes()              # after the import: sized for what came in
        self._reapply_unsaved()                    # #927: the last shutdown's unsaved writes, before the load
        self._load_hot()
        self._load_warm()                          # #748: before any write of this boot
        if journal_dir is not None:
            self.replay_tails(Path(journal_dir))
        self._worker = threading.Thread(target=self._catchup_loop, name="cbl-vec", daemon=True)
        self._worker.start()
        self._writer = threading.Thread(target=self._writer_loop, name="cbl-write", daemon=True)
        self._writer.start()
        self._wake.set()

    # ---- setup ---------------------------------------------------------------
    def _open_db(self, lib_dir):
        """(the library, the database): Couchbase Lite here; sqlstore.SQLiteBackend opens SQLite (#971)."""
        lib = CB.load(lib_dir)
        return lib, CB.Database(lib, "field", str(self.path))

    def _ensure_indexes(self) -> None:
        for c in COLLECTIONS:
            self.db.coll(c)
            if c not in COLD and not self.db.has_index(c, f"{c}_o"):
                self.db.value_index(c, f"{c}_o", "o")

    def _ensure_vector_indexes(self) -> None:
        for c, (fld, name) in VEC.items():
            if not self.db.has_index(c, name):
                n = max(self.db.count(c), 4096)
                self.db.vector_index(c, name, f"d.{fld}", DIM, int(min(4096, max(64, n ** 0.5))))

    def _meta(self, key: str):
        js = self.db.get_json(META, key)
        return json.loads(js) if js else None

    def _set_meta(self, key: str, doc: dict) -> None:
        self.db.put_json(META, key, dumps(doc))

    def _load_hot(self) -> None:
        t = time.time()
        n = 0
        with self._ram_lock:
            for c in COLLECTIONS:
                if c in COLD:
                    continue
                rows, ords = self.colls[c], self._ord[c]
                it = iter(self.db.iter_query(f"SELECT META().id AS k, o, d FROM membrane.{c} ORDER BY o"))
                try:
                    first = next(it, None)
                except CB.CBLException:
                    if not self.readonly:
                        raise
                    # a snapshot of a store from before this collection
                    # existed (the chunks, #429): nothing to read, and a
                    # read-only handle never creates it
                    continue
                for r in itertools.chain([first] if first is not None else [], it):
                    k, d = r.get("k"), r.get("d")
                    if k is None or not isinstance(d, dict):
                        continue
                    rows[k] = d
                    ords[k] = int(r.get("o") or 0)
                    n += 1
                self._next[c] = (max(ords.values()) + 1) if ords else 0
        print(f"[efield] {self.LABEL}: {n} hot rows loaded in {time.time() - t:.1f}s "
              f"({self.db.count('sentences')} sentences on disk)", file=sys.stderr)

    # ---- the one-time import of the journals --------------------------------
    @staticmethod
    def _lines(jf: Path, off: int = 0):
        """(row, end offset) per complete journal line from `off` -- a torn
        last line (no newline yet) is left for the next read."""
        with jf.open("rb") as fh:
            fh.seek(off)
            for raw in fh:
                if not raw.endswith(b"\n"):
                    return
                off += len(raw)
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict) and row.get("k"):
                    yield row, off

    @staticmethod
    def _mark(jf: Path, off: int) -> dict:
        st = jf.stat()
        return {"off": int(off), "ino": int(st.st_ino)}

    def migrate_from(self, jdir: Path) -> dict:
        """Stream every <coll>.jsonl (and a legacy <coll>.json snapshot with no
        journal) into CBL: last line wins, a tombstone purges, ordinals by
        first appearance (a delete + re-add moves to the end -- the RAM
        dict's own rule). How far each journal was read is recorded: a seat
        still writing them is caught up later from there (replay_tails)."""
        counts: dict[str, int] = {}
        marks: dict[str, dict] = {}
        t0 = time.time()
        if not jdir.is_dir():
            self._set_meta("migrated", {"from": str(jdir), "ts": time.time(), "rows": {}, "none": True})
            return counts
        print(f"[efield] couchbase lite: importing the journals under {jdir} (once)...", file=sys.stderr)
        for c in COLLECTIONS:
            jf, sf = jdir / f"{c}.jsonl", jdir / f"{c}.json"
            ords: dict[str, int] = {}
            nxt = 0
            n = 0

            def put(k, d):
                nonlocal nxt
                if not isinstance(d, dict):
                    ords.pop(k, None)
                    self.db.purge(c, k)
                    return
                if k not in ords:
                    ords[k] = nxt
                    nxt += 1
                self.db.put_json(c, k, dumps({"o": 0 if c in COLD else ords[k], "d": d}))

            self.db.begin()
            try:
                if jf.exists():
                    off = 0
                    for row, off in self._lines(jf):
                        put(row["k"], row.get("d"))
                        n += 1
                        if n % 5000 == 0:
                            self.db.end()
                            self.db.begin()
                    marks[c] = self._mark(jf, off)
                elif sf.exists():
                    try:
                        for k, d in json.loads(sf.read_text()).items():
                            put(k, d)
                            n += 1
                    except Exception:
                        pass
            finally:
                self.db.end()
            counts[c] = len(ords)
            print(f"[efield]   {c}: {n} lines -> {len(ords)} rows ({time.time() - t0:.0f}s)", file=sys.stderr)
        self._set_meta("migrated", {"from": str(jdir), "ts": time.time(), "rows": counts, "journals": marks,
                                    "seconds": round(time.time() - t0, 1)})
        return counts

    def replay_tails(self, jdir: Path) -> dict:
        """THE CATCH-UP (the switch without the long boot): the import ran
        while the old seat kept writing its journals; whatever they gained
        since replays here, through the backend's own writes. A journal
        rewritten since (a compaction: a new file, or a shorter one) replays
        whole, and rows it no longer holds are deleted."""
        mig = self._meta("migrated") or {}
        marks = dict(mig.get("journals") or {})
        done: dict[str, int] = {}
        for c in COLLECTIONS:
            jf, rec = jdir / f"{c}.jsonl", marks.get(c)
            if rec is None or not jf.exists():
                continue
            st = jf.stat()
            whole = st.st_ino != rec.get("ino") or st.st_size < int(rec.get("off") or 0)
            if not whole and st.st_size == int(rec.get("off") or 0):
                continue
            n, off, final = 0, 0 if whole else int(rec["off"]), {}
            for row, off in self._lines(jf, off):
                k, d = row["k"], row.get("d")
                if isinstance(d, dict):
                    self.upsert(c, k, d)
                    final[k] = True
                else:
                    self.delete(c, k)
                    final.pop(k, None)
                n += 1
            if whole:
                have = (list(self.colls[c]) if c not in COLD else
                        [r["k"] for r in self.db.query(f"SELECT META().id AS k FROM membrane.{c}")])
                for k in have:
                    if k not in final:
                        self.delete(c, k)
            marks[c] = self._mark(jf, off)
            done[c] = n
        if done:
            self.drain()                               # the rows land before their offsets move
            mig["journals"] = marks
            self._set_meta("migrated", mig)
            print(f"[efield] couchbase lite: journal tails replayed {done}", file=sys.stderr)
        return done

    # ---- the cold collection ---------------------------------------------------
    def _cold_get(self, key: str) -> dict | None:
        d = self._cache.get(key)
        if d is not None:
            return dict(d)
        js = self.db.get_json("sentences", key)
        if not js:
            return None
        d = json.loads(js).get("d")
        if not isinstance(d, dict):
            return None
        if len(self._cache) >= CACHE_N:
            for k in list(self._cache)[:CACHE_N // 4]:
                self._cache.pop(k, None)
        self._cache[key] = d
        return dict(d)

    def _cold_put(self, coll: str, key: str, doc: dict) -> None:
        self._cache.pop(key, None)
        self.db.put_json(coll, key, dumps({"o": 0, "d": doc}))
        self._wake.set()

    # ---- the hot collections' persistence ------------------------------------
    def _hot_put(self, coll: str, key: str, doc: dict) -> None:
        ords = self._ord[coll]
        o = ords.get(key)
        if o is None:
            o = ords[key] = self._next[coll]
            self._next[coll] = o + 1
        doc = self.colls[coll].get(key, doc)          # the RAM copy (the caller may reuse its dict)
        with self._pend_lock:
            self._pending[(coll, key)] = (o, doc)

    def _hot_del(self, coll: str, key: str) -> None:
        self._ord[coll].pop(key, None)
        with self._pend_lock:
            self._pending[(coll, key)] = (None, None)

    RETRY_MIN_S = 0.5
    RETRY_MAX_S = 30.0

    def _requeue(self, items) -> None:
        """Put unsaved writes back (issue #927): a key written again since
        the batch was taken is newer -- it stays; the failed one never
        overwrites it. The writer loop backs off (doubling) before retrying."""
        with self._pend_lock:
            for key, val in items:
                self._pending.setdefault(key, val)
        self._fails = getattr(self, "_fails", 0) + 1
        self._retry_at = time.monotonic() + min(self.RETRY_MAX_S, self.RETRY_MIN_S * 2 ** (self._fails - 1))

    def drain(self) -> int:
        """Save every pending hot write now (one transaction). A failed
        save re-queues its key (unless a newer write arrived); a failed
        begin/purge/commit re-queues the whole batch -- nothing is lost."""
        with self._pend_lock:
            todo, self._pending = self._pending, {}
            self._inflight = todo                  # the lease cut records the batch being saved too (PR 948)
        if not todo:
            return 0
        failed: list = []
        try:
            self.db.begin()
        except Exception as e:
            print(f"[efield] cbl begin failed ({e}); {len(todo)} writes re-queued", file=sys.stderr)
            self._requeue(todo.items())
            return 0
        try:
            for (c, k), (o, d) in todo.items():
                try:
                    if d is None:
                        self.db.purge(c, k)
                    else:
                        self.db.put_json(c, k, dumps({"o": o, "d": d}))
                except CB.CBLException as e:
                    print(f"[efield] cbl save {c}/{k} failed ({e}); re-queued", file=sys.stderr)
                    failed.append(((c, k), (o, d)))
        finally:
            try:
                self.db.end()
            except Exception as e:
                print(f"[efield] cbl commit failed ({e}); {len(todo)} writes re-queued", file=sys.stderr)
                failed = list(todo.items())
        if failed:
            self._requeue(failed)
            self._inflight = {}
        else:
            self._inflight = {}
            self._fails = 0
            self._retry_at = 0
        return len(todo) - len(failed)

    def _writer_loop(self) -> None:
        with CB.background():                      # its operations queue behind turns'
            self._writer_loop_body()

    def _writer_loop_body(self) -> None:
        while not self._stop:
            self._wflush.wait(self.WRITE_BEHIND_S)
            self._wflush.clear()
            if self._retry_at and time.monotonic() < self._retry_at:
                continue                           # backing off after a failed save
            try:
                self.drain()
            except Exception as e:
                print(f"[efield] cbl write-behind failed ({e})", file=sys.stderr)

    def _bump(self, coll: str) -> None:
        self._vec_dirty.add(coll)

    # ---- #748 the warm snapshot: the RAM matrices (chunks, entity states, ...)
    _warm_skip = COLD                              # no RAM rows: the CBL index serves them

    def _warm_dir(self):
        return None if self.readonly else self.path / WARM_DIR

    def _warm_proof(self, coll: str):
        """The database file's [size, mtime_ns, wal bytes]. A clean close
        checkpoints the WAL away and a reopen leaves the main file as it was
        (measured), so any write between the snapshot and this boot moves it;
        a WAL left with rows (a crash) never matches. One proof for every
        collection: one restart writes nothing in between."""
        db = self.path / "field.cblite2" / "db.sqlite3"
        try:
            st = db.stat()
        except OSError:
            return None
        try:
            wal = (db.parent / "db.sqlite3-wal").stat().st_size
        except OSError:
            wal = 0
        return [int(st.st_size), int(st.st_mtime_ns), int(wal)]

    # ---- the contract ----------------------------------------------------------
    def get(self, coll, key):
        if coll in COLD:
            return self._cold_get(key)
        return super().get(coll, key)

    def get_multi(self, coll, keys):
        if coll in COLD:
            # ONE database operation for every key (profiled 08:33: one queued operation
            # per key cost a banner hundreds of thread hand-offs)
            ks = list(dict.fromkeys(keys))
            got = self.db._run(lambda: {k: self._cold_get(k) for k in ks})
            return {k: d for k, d in got.items() if d is not None}
        return super().get_multi(coll, keys)

    def upsert(self, coll, key, doc):
        if coll in COLD:
            self._cold_put(coll, key, doc)
            return
        with self._ram_lock:
            super().upsert(coll, key, doc)
            self._hot_put(coll, key, doc)
        self._bump(coll)

    def upsert_multi(self, coll, docs):
        if not docs:
            return
        if coll in COLD:
            self.db.begin()
            try:
                for k, d in docs.items():
                    self._cold_put(coll, k, d)
            finally:
                self.db.end()
            return
        with self._ram_lock:
            super().upsert_multi(coll, docs)
            for k, d in docs.items():
                self._hot_put(coll, k, d)
        self._bump(coll)

    def insert_if_absent(self, coll, key, doc):
        if coll in COLD:
            def once():                            # check-then-insert: ONE database operation
                if key in self._cache or self.db.exists(coll, key):
                    return False
                self._cold_put(coll, key, doc)
                return True
            return self.db._run(once)
        with self._ram_lock:
            added = super().insert_if_absent(coll, key, doc)
            if added:
                self._hot_put(coll, key, doc)
        if added:
            self._bump(coll)
        return added

    def delete(self, coll, key):
        if coll in COLD:
            self._cache.pop(key, None)
            self.db.purge(coll, key)
            return
        with self._ram_lock:
            super().delete(coll, key)
            self._hot_del(coll, key)
        self._bump(coll)

    def cas_update(self, coll, key, fn):
        if coll in COLD:
            def once():                            # read-modify-write: ONE database operation
                new = fn(self._cold_get(key))
                if new is None:
                    self.delete(coll, key)
                else:
                    self._cold_put(coll, key, new)
                return new
            return self.db._run(once)
        with self._ram_lock:
            new = super().cas_update(coll, key, fn)
            if new is None:
                if key in self._ord[coll]:
                    self._hot_del(coll, key)
            else:
                self._hot_put(coll, key, new)
        self._bump(coll)
        return new

    def scan(self, coll, eq=None, order=(), limit=None):
        if coll not in COLD:
            yield from super().scan(coll, eq, order, limit)
            return
        items = [(r["k"], r["d"]) for r in self.db.query(f"SELECT META().id AS k, d FROM membrane.{coll}")
                 if isinstance(r.get("d"), dict) and matches(r["d"], eq)]
        for f in reversed(order):
            fld = f[0] if isinstance(f, tuple) else f
            rev = isinstance(f, tuple) and not str(f[1]).upper().startswith("ASC")
            items.sort(key=lambda kd, fld=fld: 0 if kd[1].get(fld) is None else kd[1].get(fld), reverse=rev)
        for i, (k, d) in enumerate(items):
            if limit is not None and i >= limit:
                break
            yield k, d

    def count(self, coll, eq=None, group=()):
        if coll in COLD and not eq and not group:
            return {"n": self.db.count(coll)}
        return super().count(coll, eq, group)

    def warm_vectors(self, coll: str, field: str, wait: bool = False) -> None:
        """#720: an indexed collection has no RAM matrix to build; its first
        query after an open is the slow one (1.07 s vs 0.17 s), so run one
        now -- in the background unless `wait`."""
        if coll not in VEC or VEC[coll][0] != field:
            return super().warm_vectors(coll, field)
        q = np.zeros(DIM, dtype=np.float32)
        q[0] = 1.0

        def _one():
            try:
                self.vector_top_k(coll, field, q, 1)
            except Exception:
                pass
        if wait:
            _one()
        else:
            import threading
            threading.Thread(target=_one, name=f"warm-{coll}", daemon=True).start()

    def vector_top_k(self, coll, field, vec, k, eq=None):
        if coll not in VEC or VEC[coll][0] != field:
            return super().vector_top_k(coll, field, vec, k, eq)
        q = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(q))
        if not n or q.size != DIM:
            return []
        q = (q / n).tolist()
        want = int(k) * (4 if eq else 1)
        dist = f"APPROX_VECTOR_DISTANCE(d.{field}, $q, 'COSINE', {NPROBES})"
        sql = f"SELECT META().id AS k, {dist} AS dist FROM membrane.{coll} ORDER BY {dist} LIMIT {want}"
        try:
            rows = self.db.query(sql, {"q": q})
        except CB.CBLException as e:
            print(f"[efield] cbl vector query failed ({e})", file=sys.stderr)
            return []
        out = [(r["k"], 1.0 - float(r.get("dist") or 0.0)) for r in rows if r.get("k")]
        if eq:
            docs = self.get_multi(coll, [kk for kk, _ in out])
            out = [(kk, s) for kk, s in out if kk in docs and matches(docs[kk], eq)][:k]
        return out

    # ---- the lazy vector index's catch-up ----------------------------------
    def catch_up(self, max_batches: int | None = None) -> int:
        tot = 0
        for c, (fld, name) in VEC.items():
            b = 0
            while not self._stop:
                n = self.db.update_lazy(c, name, unpack_vec, self.CATCHUP_BATCH)
                tot += n
                b += 1
                if not n or (max_batches and b >= max_batches):
                    break
                time.sleep(0.01)                   # the readers' turn between batches
        if tot:
            self._idx_rows += tot
            self._idx_last = time.time()
        return tot

    def _catchup_loop(self) -> None:
        with CB.background():                      # its batches queue behind turns'
            self._catchup_loop_body()

    def _catchup_loop_body(self) -> None:
        while not self._stop:
            self._wake.wait(self.CATCHUP_IDLE_S)
            if self._stop:
                return
            self._wake.clear()
            time.sleep(0.5)                        # a write burst lands first
            try:
                self.catch_up()
            except Exception as e:
                print(f"[efield] cbl vector catch-up failed ({e})", file=sys.stderr)

    def vec_status(self) -> dict:
        st = super().vec_status()
        st["cbl"] = {"sentences_on_disk": self.db.count("sentences"), "indexed_since_boot": self._idx_rows,
                     "last_catchup_ts": self._idx_last, "cache": len(self._cache)}
        return st

    def flush(self) -> None:
        """The pending hot writes, saved now."""
        self.drain()

    def _unsaved_path(self) -> Path:
        return self.path.parent / f"{self.path.name}.unsaved.json"

    def _reapply_unsaved(self) -> None:
        """Issue #927: the record a cut-off shutdown left beside the database
        carries the unsaved (o, d) values. Put them back -- a row already on
        disk with a greater `o` is newer and wins. The record is marked
        `reapplied` (never applied twice: a later edit must not be clobbered)
        and stays for `restarts.boot_text` to announce."""
        self.recovered_at_boot = None
        rp = self._unsaved_path()
        try:
            rec = json.loads(rp.read_text())
        except Exception:
            return
        if not isinstance(rec, dict) or rec.get("reapplied"):
            return
        applied = stale = 0
        todo = []
        for it in rec.get("items") or []:
            try:
                c, k, o, d = it["c"], it["k"], it.get("o"), it.get("d")
            except (KeyError, TypeError):
                continue
            if c not in COLLECTIONS or c in COLD:
                continue
            disk = None
            try:
                js = self.db.get_json(c, k)
                disk = json.loads(js).get("o") if js else None
            except Exception:
                pass
            if d is not None and disk is not None and o is not None and disk > o:
                stale += 1
                continue
            todo.append((c, k, o, d))
        try:
            self.db.begin()
            try:
                for c, k, o, d in todo:
                    if d is None:
                        self.db.purge(c, k)
                    else:
                        self.db.put_json(c, k, dumps({"o": o, "d": d}))
                    applied += 1
            finally:
                self.db.end()
        except Exception as e:
            print(f"[efield] could not re-apply the unsaved record ({e})", file=sys.stderr)
            return                                  # the record stays; the next boot tries again
        self.recovered_at_boot = {"applied": applied, "stale": stale}
        rec.update(self.recovered_at_boot, reapplied=True)
        try:
            rp.write_text(json.dumps(rec))
        except Exception:
            pass

    def _record_unsaved(self, lock_timeout=None) -> None:
        """Issue #927: a shutdown whose last drain still left writes pending
        must not lose them silently -- write what was left to a record file
        beside the database, say so on stderr (the operator's log), and keep
        the list in `unsaved_at_close`."""
        # the lease cut passes a bound: a drain hung on the lock must not hang the record too
        got = self._pend_lock.acquire() if lock_timeout is None \
            else self._pend_lock.acquire(timeout=lock_timeout)
        try:
            snap = dict(getattr(self, "_inflight", None) or {})
            snap.update(self._pending)             # a newer pending write wins over the batch in flight
        finally:
            if got:
                self._pend_lock.release()
        left = sorted(f"{c}/{k}" for (c, k) in snap)
        items = [{"c": c, "k": k, "o": v[0], "d": v[1]}
                 for (c, k), v in sorted(snap.items())]
        self.unsaved_at_close = left
        if not left:
            return
        msg = (f"[efield] SHUTDOWN LEFT {len(left)} WRITE(S) UNSAVED "
               f"(busy database): {', '.join(left[:10])}"
               + (" ..." if len(left) > 10 else ""))
        print(msg, file=sys.stderr)
        try:
            self._unsaved_path().write_text(json.dumps(
                {"ts": time.time(), "count": len(left), "keys": left, "items": items}))
        except Exception as e:
            print(f"[efield] could not write the unsaved record ({e})", file=sys.stderr)

    def close(self) -> None:
        self._stop = True
        self._wake.set()
        self._wflush.set()
        for t in (self._worker, self._writer):
            try:
                if t is not None:
                    t.join(timeout=5)
            except Exception:
                pass
        self.drain()
        self._record_unsaved()
        self.db.close()
        if self._lock_fd is not None:            # the lock rode the fd;
            os.close(self._lock_fd)              # released, the next writer may enter
            self._lock_fd = None


def import_only(path, journal_dir, lib_dir=None) -> dict:
    """THE LEAN PRE-IMPORT (2026-10-10): the journals into the database and
    nothing else -- no hot rows in RAM, no catch-up thread -- so it can run
    beside a live seat on a phone with little memory to spare (the full
    rehearsal's 1.1 GB peak beside the seat's 2.9 GB woke the low-memory
    killer). The switch-over boot then loads and replays the tails only."""
    shell = CBLiteBackend.__new__(CBLiteBackend)
    shell.path = Path(path)
    shell.lib, shell.db = shell._open_db(lib_dir)      # the backend's own kind (#971: SQLite or CBL)
    db = shell.db
    try:
        CBLiteBackend._ensure_indexes(shell)
        out = {} if shell._meta("migrated") else CBLiteBackend.migrate_from(shell, Path(journal_dir))
        CBLiteBackend._ensure_vector_indexes(shell)
        return out
    finally:
        db.close()


if __name__ == "__main__":
    # python -m membrane.cblstore <db dir> <journal dir>
    import resource
    t = time.time()
    rows = import_only(sys.argv[1], sys.argv[2])
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(f"imported {rows} in {time.time() - t:.0f}s, peak RSS "
          f"{peak // (1024 * 1024) if sys.platform == 'darwin' else peak // 1024} MB")
