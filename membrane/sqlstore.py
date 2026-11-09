"""THE SQLITE HOME (#971, the operator 2026-11-08): the field's store on SQLite, a parallel option to
Couchbase Lite and the default for new seats.

It is `cblstore.CBLiteBackend` with `sqlitedb.Database` underneath: the same hot/cold split (the hot
collections in RAM with the database as their persistence, write-behind; `sentences` cold, read on
demand), the same writer lock, warm snapshot and journal import. Only two things differ:

- the database: one SQLite file, `<dir>/field.sqlite3` (WAL), public domain, shipped in the wheel;
- the sentence vectors: no vector index in the database -- a brute-force search over a float16 matrix
  kept beside it (`<dir>/vectors/sentences.f16` + `.keys`), memory-mapped so the OS pages it in instead
  of the process holding it. New rows are appended by the same lazy catch-up thread; a deleted row is
  masked; the whole matrix is rebuilt from the rows when it no longer matches them. Exact search, so
  recall is the matrix's (no IVF probes).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import numpy as np

from . import sqlitedb as SDB
from .cblstore import COLD, DIM, VEC, CBLiteBackend, StoreBusy, unpack_vec  # noqa: F401
from .efstore import matches

KIND = "sqlite"
CHUNK = 65536                                  # rows per block of the brute-force product (memory, not a cutoff)


class _Joined:
    """Readonly view: the mapped file rows (snapshotted at open) followed by the RAM overlay rows."""

    def __init__(self, base, ram):
        self.base, self.ram = base, ram
        self.nb = 0 if base is None else len(base)

    def __getitem__(self, sl):
        a, b = sl.start or 0, sl.stop
        parts = []
        if self.base is not None and a < self.nb:
            parts.append(np.asarray(self.base[a:min(b, self.nb)]))
        if self.ram is not None and b > self.nb:
            parts.append(np.asarray(self.ram[max(a - self.nb, 0):b - self.nb]))
        return parts[0] if len(parts) == 1 else np.concatenate(parts)


class _Matrix:
    """The sentence vectors: an append-only float16 file + its keys, memory-mapped for search."""

    def __init__(self, d: Path, readonly: bool = False):
        self.d = d
        self.readonly = readonly
        self._ram = None                       # readonly: the vectors live in memory, never in the file
        if not readonly:
            d.mkdir(parents=True, exist_ok=True)
        self.f = d / "sentences.f16"
        self.kf = d / "sentences.keys"
        self.lock = threading.RLock()
        self.keys: list[str] = []
        self.row: dict[str, int] = {}
        self.dead: set[int] = set()
        self._mm = None
        self._base_n = 0                       # readonly: file rows at open (the file may grow under us)
        if self.f.exists() and self.kf.exists():
            try:
                txt = self.kf.read_text()
                if txt.lstrip().startswith("["):           # the old one-list format
                    self.keys = json.loads(txt)
                    if not self.readonly:
                        self.kf.write_text("".join(json.dumps(k) + "\n" for k in self.keys))
                else:
                    self.keys = [json.loads(ln) for ln in txt.splitlines() if ln]
                n = self.f.stat().st_size // (2 * DIM)
                if n != len(self.keys):
                    raise ValueError("vectors and keys disagree")
                self.row = {k: i for i, k in enumerate(self.keys)}
                self._base_n = n if self.readonly else 0
            except Exception:
                self.reset()

    def reset(self) -> None:
        with self.lock:
            self.keys, self.row, self.dead, self._mm = [], {}, set(), None
            self._ram, self._base_n = None, 0
            for p in (() if self.readonly else (self.f, self.kf)):
                try:
                    p.unlink()
                except OSError:
                    pass

    def __len__(self):
        return len(self.keys)

    def append(self, pairs) -> int:
        """[(key, unit float32 vec)] -> appended (a re-written key: its old row masked)."""
        pairs = [(k, v) for k, v in pairs if v is not None and np.asarray(v).size == DIM]
        if not pairs:
            return 0
        with self.lock:
            a = np.stack([np.asarray(v, np.float32) for _, v in pairs]).astype(np.float16)
            if self.readonly:
                self._ram = a if self._ram is None else np.concatenate([self._ram, a])
            else:
                with self.f.open("ab") as fh:
                    fh.write(a.tobytes())
                with self.kf.open("a") as fh:           # append-only, one JSON key per line
                    fh.write("".join(json.dumps(k) + "\n" for k, _ in pairs))
            for k, _ in pairs:
                old = self.row.get(k)
                if old is not None:
                    self.dead.add(old)
                self.row[k] = len(self.keys)
                self.keys.append(k)
            self._mm = None
        return len(pairs)

    def drop(self, key: str) -> None:
        with self.lock:
            i = self.row.pop(key, None)
            if i is not None:
                self.dead.add(i)

    def _map(self):
        if self.readonly:
            if self._mm is None and self._base_n:
                self._mm = np.memmap(self.f, dtype=np.float16, mode="r", shape=(self._base_n, DIM))
            if self._mm is None:
                return self._ram
            return self._mm if self._ram is None else _Joined(self._mm, self._ram)
        if self._mm is None and self.keys:
            self._mm = np.memmap(self.f, dtype=np.float16, mode="r", shape=(len(self.keys), DIM))
        return self._mm

    def top(self, q: np.ndarray, want: int) -> list[tuple[str, float]]:
        with self.lock:
            mm, keys, dead = self._map(), list(self.keys), set(self.dead)
        if mm is None or not keys:
            return []
        best_i, best_s = np.empty(0, np.int64), np.empty(0, np.float32)
        for s in range(0, len(keys), CHUNK):
            block = np.asarray(mm[s:s + CHUNK], dtype=np.float32)
            sc = block @ q
            if dead:
                for i in dead:
                    if s <= i < s + len(sc):
                        sc[i - s] = -np.inf
            take = min(want, len(sc))
            idx = np.argpartition(-sc, take - 1)[:take] if take < len(sc) else np.arange(len(sc))
            best_i = np.concatenate([best_i, idx + s])
            best_s = np.concatenate([best_s, sc[idx]])
        order = np.argsort(-best_s)[:want]
        return [(keys[int(best_i[j])], float(best_s[j])) for j in order if np.isfinite(best_s[j])]


class SQLiteBackend(CBLiteBackend):
    KIND = KIND
    LABEL = "sqlite"
    readonly = False                          # (import_only's __new__ shell reads it too)
    _mx = None                                # (defaults: cblstore.import_only builds a backend without __init__)
    _seeded = False
    _sdb_path = None

    def __init__(self, path, journal_dir=None, lib_dir=None, readonly=False):
        self._sdb_path = Path(path)
        self._mx = None
        self._pending_vec = {}
        self._seeded = False
        super().__init__(path, journal_dir=journal_dir, lib_dir=lib_dir, readonly=readonly)

    # the database: SQLite in place of CBL (CBLiteBackend.__init__ calls these two)
    def _open_db(self, lib_dir):
        return None, SDB.Database("field", str(self._sdb_path or self.path))

    def _vectors(self) -> _Matrix:
        if self._mx is None:
            self._mx = _Matrix(Path(self._sdb_path or self.path) / "vectors", readonly=bool(self.readonly))
        return self._mx

    @property
    def _pending_vec(self) -> dict:
        return self.__dict__.setdefault("_pv", {})

    @_pending_vec.setter
    def _pending_vec(self, v) -> None:
        self.__dict__["_pv"] = v

    def _ensure_vector_indexes(self) -> None:
        mx = self._vectors()
        n = self.db.count("sentences")
        # dead masks are not persisted: rebuild whenever the matrix and the database differ at all
        if len(mx.keys) != len(mx.row) or (len(mx.row) != n and (n or mx.keys)):
            mx.reset()

    def _cold_put(self, coll: str, key: str, doc: dict) -> None:
        super()._cold_put(coll, key, doc)
        if coll in VEC:
            with self._vectors().lock:
                self._pending_vec[key] = doc.get(VEC[coll][0])

    def delete(self, coll, key):
        super().delete(coll, key)
        if coll in VEC:
            self._vectors().drop(key)
            self._pending_vec.pop(key, None)

    # ---- the catch-up: new sentence vectors into the matrix ---------------------------------------
    def catch_up(self, max_batches: int | None = None, wait: bool = True) -> int:
        mx = self._vectors()
        if not wait:                                   # a search never queues behind a running seed
            lk = self.__dict__.setdefault("_cu_lock", threading.Lock())
            if not lk.acquire(blocking=False):
                return 0
            try:
                return self._catch_up(mx, max_batches)
            finally:
                lk.release()
        # one catch-up at a time (its own lock); mx.lock is taken only inside mx.append / the pending swap,
        # so writes and searches are never held behind the seed scan
        with self.__dict__.setdefault("_cu_lock", threading.Lock()):
            return self._catch_up(mx, max_batches)

    def _catch_up(self, mx, max_batches) -> int:
        tot = 0
        if not getattr(self, "_seeded", False):        # the first catch-up: every row the matrix lacks
            have = set(mx.row)
            seeded_keys = set()
            todo = []
            # drain the stream first: the db lock is held only for the raw read, never across the
            # unpack/append work, and mx.lock only inside mx.append
            rows = list(self.db.iter_query("SELECT META().id AS k, d.vec AS v FROM membrane.sentences"))
            for r in rows:
                if r.get("k") not in have and r.get("v"):
                    todo.append((r["k"], unpack_vec(r["v"])))
                    seeded_keys.add(r["k"])
                    if len(todo) >= self.CATCHUP_BATCH:
                        tot += mx.append(todo)
                        todo = []
            tot += mx.append(todo)
            self._seeded = True
        else:
            seeded_keys = set()
        with mx.lock:
            pend, self._pending_vec = self._pending_vec, {}
        pend = {k: v for k, v in pend.items() if k not in seeded_keys}    # the scan already read these
        tot += mx.append([(k, unpack_vec(v)) for k, v in pend.items() if v])
        if tot:
            self._idx_rows += tot
            self._idx_last = time.time()
        return tot

    def vector_top_k(self, coll, field, vec, k, eq=None):
        if coll not in VEC or VEC[coll][0] != field:
            return super(CBLiteBackend, self).vector_top_k(coll, field, vec, k, eq)
        q = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(q))
        if not n or q.size != DIM:
            return []
        if not getattr(self, "_seeded", False) or self._pending_vec:
            self.catch_up(wait=False)
        mx = self._vectors()
        if not eq:
            return mx.top(q / n, int(k))
        want = int(k) * 4
        while True:                                    # widen until k matches or the matrix is exhausted
            out = mx.top(q / n, want)
            docs = self.get_multi(coll, [kk for kk, _ in out])
            hit = [(kk, s) for kk, s in out if kk in docs and matches(docs[kk], eq)]
            if len(hit) >= k or len(out) < want or want >= len(mx.keys):
                return hit[:k]
            want *= 4

    def _warm_proof(self, coll: str):
        db = Path(self.db.file)
        try:
            st = db.stat()
        except OSError:
            return None
        try:
            wal = Path(str(db) + "-wal").stat().st_size
        except OSError:
            wal = 0
        return [int(st.st_size), int(st.st_mtime_ns), int(wal)]

    def vec_status(self) -> dict:
        st = super(CBLiteBackend, self).vec_status()
        mx = self._vectors()
        st["sqlite"] = {"sentences_on_disk": self.db.count("sentences"), "vectors": len(mx.row),
                        "masked": len(mx.dead), "indexed_since_boot": self._idx_rows, "last_catchup_ts": self._idx_last}
        return st
