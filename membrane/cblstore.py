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
import json
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np

from . import cblite as CB
from .efstore import COLLECTIONS, MemBackend, matches

COLD = ("sentences",)
VEC = {"sentences": ("vec", "sent_vec")}            # coll -> (field, index name)
DIM = 1024
META = "meta"
CACHE_N = 4096                                     # decoded cold docs kept (the gate re-reads its sids)


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
    CATCHUP_BATCH = 500
    CATCHUP_IDLE_S = 30.0

    def __init__(self, path, journal_dir=None, lib_dir=None):
        super().__init__()
        self.path = Path(path)
        self.lib = CB.load(lib_dir)
        self.db = CB.Database(self.lib, "field", str(self.path))
        self._ord: dict[str, dict[str, int]] = {c: {} for c in COLLECTIONS}
        self._next: dict[str, int] = {c: 0 for c in COLLECTIONS}
        self._cache: dict[str, dict] = {}
        self._wake = threading.Event()
        self._stop = False
        self._idx_rows = 0
        self._idx_last = 0.0
        self._ensure_indexes()
        if journal_dir is not None and self._meta("migrated") is None:
            self.migrate_from(Path(journal_dir))
        self._ensure_vector_indexes()              # after the import: sized for what came in
        self._load_hot()
        if journal_dir is not None:
            self.replay_tails(Path(journal_dir))
        self._worker = threading.Thread(target=self._catchup_loop, name="cbl-vec", daemon=True)
        self._worker.start()
        self._wake.set()

    # ---- setup ---------------------------------------------------------------
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
                for r in self.db.iter_query(f"SELECT META().id AS k, o, d FROM membrane.{c} ORDER BY o"):
                    k, d = r.get("k"), r.get("d")
                    if k is None or not isinstance(d, dict):
                        continue
                    rows[k] = d
                    ords[k] = int(r.get("o") or 0)
                    n += 1
                self._next[c] = (max(ords.values()) + 1) if ords else 0
        print(f"[efield] couchbase lite: {n} hot rows loaded in {time.time() - t:.1f}s "
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
        self.db.put_json(coll, key, dumps({"o": o, "d": doc}))

    def _hot_del(self, coll: str, key: str) -> None:
        self._ord[coll].pop(key, None)
        self.db.purge(coll, key)

    def _bump(self, coll: str) -> None:
        self._vec_dirty.add(coll)

    # ---- the contract ----------------------------------------------------------
    def get(self, coll, key):
        if coll in COLD:
            return self._cold_get(key)
        return super().get(coll, key)

    def get_multi(self, coll, keys):
        if coll in COLD:
            out = {}
            for k in dict.fromkeys(keys):
                d = self._cold_get(k)
                if d is not None:
                    out[k] = d
            return out
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
            self.db.begin()
            try:
                for k, d in docs.items():
                    self._hot_put(coll, k, d)
            finally:
                self.db.end()
        self._bump(coll)

    def insert_if_absent(self, coll, key, doc):
        if coll in COLD:
            with self.db.lock:
                if key in self._cache or self.db.exists(coll, key):
                    return False
                self._cold_put(coll, key, doc)
                return True
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
            with self.db.lock:
                new = fn(self._cold_get(key))
                if new is None:
                    self.delete(coll, key)
                else:
                    self._cold_put(coll, key, new)
                return new
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

    def vector_top_k(self, coll, field, vec, k, eq=None):
        if coll not in VEC or VEC[coll][0] != field:
            return super().vector_top_k(coll, field, vec, k, eq)
        q = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(q))
        if not n or q.size != DIM:
            return []
        q = (q / n).tolist()
        want = int(k) * (4 if eq else 1)
        sql = (f"SELECT META().id AS k, APPROX_VECTOR_DISTANCE(d.{field}, $q, 'COSINE') AS dist "
               f"FROM membrane.{coll} ORDER BY APPROX_VECTOR_DISTANCE(d.{field}, $q, 'COSINE') LIMIT {want}")
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
        """Every write is already saved (the journals' flush has no work here)."""

    def close(self) -> None:
        self._stop = True
        self._wake.set()
        try:
            self._worker.join(timeout=5)
        except Exception:
            pass
        self.db.close()


def import_only(path, journal_dir, lib_dir=None) -> dict:
    """THE LEAN PRE-IMPORT (2026-10-10): the journals into the database and
    nothing else -- no hot rows in RAM, no catch-up thread -- so it can run
    beside a live seat on a phone with little memory to spare (the full
    rehearsal's 1.1 GB peak beside the seat's 2.9 GB woke the low-memory
    killer). The switch-over boot then loads and replays the tails only."""
    lib = CB.load(lib_dir)
    db = CB.Database(lib, "field", str(path))
    shell = CBLiteBackend.__new__(CBLiteBackend)
    shell.db, shell.lib = db, lib
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
