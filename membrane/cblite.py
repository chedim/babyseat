"""COUCHBASE LITE, BY CTYPES (2026-10-10): the smallest binding over CBL-C
3.2 Enterprise the field's store needs -- open (+ the Android init),
collections, JSON documents, transactions, SQL++ with parameters, value
indexes, a LAZY vector index and its updater. No build step: the same
file runs on the Mac (the tests) and in Termux on the seat's phone.

The libraries are NOT in git (scripts/fetch-cbl.sh puts them under
vendor/cbl/<platform>/, checksummed). Every call into the library takes
the database's one lock: a compiled query's parameters are shared state,
and the field's lanes call in from many threads."""
from __future__ import annotations

import ctypes as C
import json
import os
import sys
import threading
from pathlib import Path


class FLSlice(C.Structure):
    _fields_ = [("buf", C.c_void_p), ("size", C.c_size_t)]


class FLSliceResult(C.Structure):
    _fields_ = [("buf", C.c_void_p), ("size", C.c_size_t)]


class CBLError(C.Structure):
    _fields_ = [("domain", C.c_uint8), ("code", C.c_int), ("internal_info", C.c_uint)]


class CBLEncryptionKey(C.Structure):
    _fields_ = [("algorithm", C.c_uint32), ("bytes", C.c_uint8 * 32)]


class CBLDatabaseConfiguration(C.Structure):
    _fields_ = [("directory", FLSlice), ("encryptionKey", CBLEncryptionKey), ("fullSync", C.c_bool)]


class CBLValueIndexConfiguration(C.Structure):
    _fields_ = [("expressionLanguage", C.c_uint32), ("expressions", FLSlice), ("where", FLSlice)]


class CBLVectorIndexConfiguration(C.Structure):
    _fields_ = [("expressionLanguage", C.c_uint32), ("expression", FLSlice), ("dimensions", C.c_uint),
                ("centroids", C.c_uint), ("isLazy", C.c_bool), ("encoding", C.c_void_p),
                ("metric", C.c_uint32), ("minTrainingSize", C.c_uint), ("maxTrainingSize", C.c_uint),
                ("numProbes", C.c_uint)]


class CBLInitContext(C.Structure):
    _fields_ = [("filesDir", C.c_char_p), ("tempDir", C.c_char_p)]


N1QL = 1
COSINE = 2
LOG_WARNING = 3


def S(s) -> FLSlice:
    """A slice over a fresh buffer; the buffer rides the slice object (alive
    as long as the caller holds the slice -- no shared keep-alive list)."""
    if s is None:
        return FLSlice(None, 0)
    b = s.encode() if isinstance(s, str) else bytes(s)
    buf = C.create_string_buffer(b, len(b))
    sl = FLSlice(C.cast(buf, C.c_void_p), len(b))
    sl._buf = buf
    return sl


class CBLException(Exception):
    pass


def is_android() -> bool:
    return hasattr(sys, "getandroidapilevel") or bool(os.environ.get("ANDROID_ROOT"))


def platform_dir() -> str:
    if is_android():
        return "android"
    if sys.platform == "darwin":
        return "macos"
    import platform
    return "linux-arm64" if platform.machine() in ("aarch64", "arm64") else "linux-x86_64"


def default_dir() -> Path:
    env = os.environ.get("MEMBRANE_CBL_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "vendor" / "cbl" / platform_dir()


def lib_paths(d: Path | None = None) -> tuple[Path, Path] | None:
    """(libcblite, the vector search extension's dir/framework) or None."""
    d = Path(d) if d else default_dir()
    for name in ("libcblite.so", "libcblite.dylib"):
        lib = d / name
        if lib.exists():
            break
    else:
        return None
    fw = d / "CouchbaseLiteVectorSearch.framework"
    if fw.exists():
        return lib, fw
    if (d / "libCouchbaseLiteVectorSearch.so").exists() or (d / "CouchbaseLiteVectorSearch.so").exists():
        return lib, d
    return None


def extension_binary(vs_path: Path) -> Path | None:
    """The vector search extension's loadable file inside what lib_paths()
    names: the framework's binary, or the .so in the directory."""
    vs_path = Path(vs_path)
    for cand in (vs_path / "Versions" / "A" / "CouchbaseLiteVectorSearch",
                 vs_path / "CouchbaseLiteVectorSearch",
                 vs_path / "libCouchbaseLiteVectorSearch.so",
                 vs_path / "CouchbaseLiteVectorSearch.so"):
        if cand.is_file():
            return cand
    return None


def pin_extension(vs_path: Path):
    """Load the vector search extension once, RTLD_NODELETE, and never let
    it go (issue #442). It registers a pthread_atfork handler; LiteCore
    loads it into each SQLite connection and SQLite dlcloses it when the
    connection closes, so after the last store closed it was unmapped with
    its handler still registered, and the next fork() (every subprocess
    with close_fds on macOS) jumped into unmapped memory. Pinned, the
    loader's own reference keeps it mapped for the life of the process."""
    binary = extension_binary(vs_path)
    if binary is None:
        return None
    return C.CDLL(str(binary), mode=os.RTLD_NOW | os.RTLD_GLOBAL | getattr(os, "RTLD_NODELETE", 0))


_LIB = None
_LIB_LOCK = threading.Lock()


def load(d: Path | None = None) -> "Lib":
    """The process's one library (CBL_Init and the vector search extension
    may each be set up once)."""
    global _LIB
    with _LIB_LOCK:
        if _LIB is None:
            p = lib_paths(d)
            if p is None:
                raise CBLException(f"no Couchbase Lite libraries under {d or default_dir()}")
            _LIB = Lib(str(p[0]), str(p[1]))
        return _LIB


class Lib:
    def __init__(self, path: str, vs_path: str):
        L = self.L = C.CDLL(path)
        vp, b = C.c_void_p, C.c_bool
        E = C.POINTER(CBLError)
        sig = {
            "CBLLog_SetConsoleLevel": (None, [C.c_uint8]),
            "CBL_EnableVectorSearch": (b, [FLSlice, E]),
            "CBLError_Message": (FLSliceResult, [E]),
            "_FLBuf_Release": (None, [vp]),
            "CBL_Release": (None, [vp]),
            "CBLDatabaseConfiguration_Default": (CBLDatabaseConfiguration, []),
            "CBLDatabase_Open": (vp, [FLSlice, C.POINTER(CBLDatabaseConfiguration), E]),
            "CBLDatabase_Close": (b, [vp, E]),
            "CBLDatabase_CreateCollection": (vp, [vp, FLSlice, FLSlice, E]),
            "CBLDatabase_BeginTransaction": (b, [vp, E]),
            "CBLDatabase_EndTransaction": (b, [vp, b, E]),
            "CBLDocument_CreateWithID": (vp, [FLSlice]),
            "CBLDocument_SetJSON": (b, [vp, FLSlice, E]),
            "CBLDocument_CreateJSON": (FLSliceResult, [vp]),
            "CBLCollection_SaveDocument": (b, [vp, vp, E]),
            "CBLCollection_GetDocument": (vp, [vp, FLSlice, E]),
            "CBLCollection_PurgeDocumentByID": (b, [vp, FLSlice, E]),
            "CBLCollection_Count": (C.c_uint64, [vp]),
            "CBLCollection_CreateValueIndex": (b, [vp, FLSlice, CBLValueIndexConfiguration, E]),
            "CBLCollection_CreateVectorIndex": (b, [vp, FLSlice, CBLVectorIndexConfiguration, E]),
            "CBLCollection_GetIndex": (vp, [vp, FLSlice, E]),
            "CBLVectorEncoding_CreateScalarQuantizer": (vp, [C.c_uint32]),
            "CBLVectorEncoding_Free": (None, [vp]),
            "CBLQueryIndex_BeginUpdate": (vp, [vp, C.c_size_t, E]),
            "CBLIndexUpdater_Count": (C.c_size_t, [vp]),
            "CBLIndexUpdater_Value": (vp, [vp, C.c_size_t]),
            "CBLIndexUpdater_SetVector": (b, [vp, C.c_size_t, C.POINTER(C.c_float), C.c_size_t, E]),
            "CBLIndexUpdater_SkipVector": (None, [vp, C.c_size_t]),
            "CBLIndexUpdater_Finish": (b, [vp, E]),
            "CBLDatabase_CreateQuery": (vp, [vp, C.c_uint32, FLSlice, C.POINTER(C.c_int), E]),
            "CBLQuery_SetParameters": (None, [vp, vp]),
            "CBLQuery_Execute": (vp, [vp, E]),
            "CBLResultSet_Next": (b, [vp]),
            "CBLResultSet_ResultDict": (vp, [vp]),
            "FLValue_ToJSON": (FLSliceResult, [vp]),
            "FLValue_AsString": (FLSlice, [vp]),
            "FLDoc_FromJSON": (vp, [FLSlice, C.POINTER(C.c_int)]),
            "FLDoc_GetRoot": (vp, [vp]),
            "FLValue_AsDict": (vp, [vp]),
            "FLDoc_Release": (None, [vp]),
        }
        for name, (res, args) in sig.items():
            f = getattr(L, name)
            f.restype, f.argtypes = res, args
        L.CBLLog_SetConsoleLevel(LOG_WARNING)
        if hasattr(L, "CBL_Init"):                       # the Android build: its files + temp dirs first
            home = os.environ.get("CBL_HOME") or os.path.join(os.path.expanduser("~"), ".cbl")
            tmp = os.path.join(home, "tmp")
            os.makedirs(tmp, exist_ok=True)
            L.CBL_Init.restype, L.CBL_Init.argtypes = b, [CBLInitContext, E]
            self._init = CBLInitContext(home.encode(), tmp.encode())
            err = CBLError()
            self.check(L.CBL_Init(self._init, C.byref(err)), err, "init")
        self._vs_pin = pin_extension(Path(vs_path))      # before anything can load it (#442)
        err = CBLError()
        self.check(L.CBL_EnableVectorSearch(S(vs_path), C.byref(err)), err, "enable vector search")

    def take(self, r: FLSliceResult) -> bytes:
        if not r.buf:
            return b""
        out = C.string_at(r.buf, r.size)
        self.L._FLBuf_Release(r.buf)
        return out

    def check(self, ok, err: CBLError, what: str):
        if not ok and err.code:
            msg = self.take(self.L.CBLError_Message(C.byref(err))).decode(errors="replace")
            raise CBLException(f"{what}: {msg} (domain {err.domain} code {err.code})")
        return ok


class Database:
    """One CBL database; every call under its lock."""

    def __init__(self, lib: Lib, name: str, directory: str, scope: str = "membrane"):
        self.lib, L = lib, lib.L
        self.lock = threading.RLock()
        self.scope = scope
        os.makedirs(directory, exist_ok=True)
        cfg = L.CBLDatabaseConfiguration_Default()
        d = S(directory)
        cfg.directory = d
        err = CBLError()
        self.h = lib.check(L.CBLDatabase_Open(S(name), C.byref(cfg), C.byref(err)), err, "open")
        self.colls: dict[str, int] = {}
        self._q: dict[str, int] = {}

    def coll(self, name: str):
        h = self.colls.get(name)
        if h is None:
            with self.lock:
                err = CBLError()
                h = self.colls[name] = self.lib.check(
                    self.lib.L.CBLDatabase_CreateCollection(self.h, S(name), S(self.scope), C.byref(err)),
                    err, f"collection {name}")
        return h

    # ---- transactions --------------------------------------------------------
    def begin(self):
        self.lock.acquire()
        err = CBLError()
        try:
            self.lib.check(self.lib.L.CBLDatabase_BeginTransaction(self.h, C.byref(err)), err, "begin")
        except Exception:
            self.lock.release()
            raise

    def end(self, commit=True):
        try:
            err = CBLError()
            self.lib.check(self.lib.L.CBLDatabase_EndTransaction(self.h, commit, C.byref(err)), err, "end")
        finally:
            self.lock.release()

    # ---- documents -----------------------------------------------------------
    def put_json(self, coll: str, key: str, js: str):
        L, err = self.lib.L, CBLError()
        with self.lock:
            d = L.CBLDocument_CreateWithID(S(key))
            try:
                self.lib.check(L.CBLDocument_SetJSON(d, S(js), C.byref(err)), err, "set json")
                self.lib.check(L.CBLCollection_SaveDocument(self.coll(coll), d, C.byref(err)), err, "save")
            finally:
                L.CBL_Release(d)

    def get_json(self, coll: str, key: str) -> bytes | None:
        if not key:                              # a None/'' key asked the
            return None                          # native lib for the doc ''
        L, err = self.lib.L, CBLError()          # ('Invalid document ID',
        with self.lock:                          # issue #11) -- matches nothing
            d = L.CBLCollection_GetDocument(self.coll(coll), S(key), C.byref(err))
        with self.lock:
            d = L.CBLCollection_GetDocument(self.coll(coll), S(key), C.byref(err))
            if not d:
                self.lib.check(False, err, "get")
                return None
            try:
                return self.lib.take(L.CBLDocument_CreateJSON(d))
            finally:
                L.CBL_Release(d)

    def exists(self, coll: str, key: str) -> bool:
        if not key:                              # '' can never be stored
            return False
        L, err = self.lib.L, CBLError()
        with self.lock:
            d = L.CBLCollection_GetDocument(self.coll(coll), S(key), C.byref(err))
            if d:
                L.CBL_Release(d)
            return bool(d)

    def purge(self, coll: str, key: str):
        with self.lock:
            err = CBLError()
            self.lib.L.CBLCollection_PurgeDocumentByID(self.coll(coll), S(key), C.byref(err))

    def count(self, coll: str) -> int:
        with self.lock:
            return int(self.lib.L.CBLCollection_Count(self.coll(coll)))

    # ---- indexes -------------------------------------------------------------
    def value_index(self, coll: str, name: str, exprs: str):
        with self.lock:
            err = CBLError()
            cfg = CBLValueIndexConfiguration(N1QL, S(exprs), S(None))
            self.lib.check(self.lib.L.CBLCollection_CreateValueIndex(self.coll(coll), S(name), cfg, C.byref(err)),
                           err, f"value index {name}")

    def has_index(self, coll: str, name: str) -> bool:
        with self.lock:
            err = CBLError()
            ix = self.lib.L.CBLCollection_GetIndex(self.coll(coll), S(name), C.byref(err))
            if ix:
                self.lib.L.CBL_Release(ix)
            return bool(ix)

    def vector_index(self, coll: str, name: str, expr: str, dims: int, centroids: int,
                     probes: int = 0, sq: int = 8, lazy: bool = True):
        L = self.lib.L
        with self.lock:
            err = CBLError()
            enc = L.CBLVectorEncoding_CreateScalarQuantizer(sq)
            try:
                cfg = CBLVectorIndexConfiguration(N1QL, S(expr), dims, centroids, lazy, enc, COSINE, 0, 0, probes)
                self.lib.check(L.CBLCollection_CreateVectorIndex(self.coll(coll), S(name), cfg, C.byref(err)),
                               err, f"vector index {name}")
            finally:
                L.CBLVectorEncoding_Free(enc)

    def update_lazy(self, coll: str, name: str, to_vec, limit: int = 500) -> int:
        """ONE batch of a lazy vector index: up to `limit` rows it still
        wants; `to_vec(str) -> float32 array | None` (None = skip the row).
        Returns how many rows the batch held (0 = caught up)."""
        import numpy as np
        L = self.lib.L
        with self.lock:
            err = CBLError()
            ix = self.lib.check(L.CBLCollection_GetIndex(self.coll(coll), S(name), C.byref(err)), err, "index")
            if not ix:
                return 0
            try:
                up = L.CBLQueryIndex_BeginUpdate(ix, limit, C.byref(err))
                if not up:
                    self.lib.check(False, err, "begin update")
                    return 0
                try:
                    n = L.CBLIndexUpdater_Count(up)
                    for i in range(n):
                        sl = L.FLValue_AsString(L.CBLIndexUpdater_Value(up, i))
                        try:
                            v = to_vec(C.string_at(sl.buf, sl.size).decode()) if sl.buf else None
                        except Exception:
                            v = None
                        if v is None:
                            L.CBLIndexUpdater_SkipVector(up, i)
                            continue
                        a = np.ascontiguousarray(v, dtype=np.float32)
                        self.lib.check(L.CBLIndexUpdater_SetVector(
                            up, i, a.ctypes.data_as(C.POINTER(C.c_float)), a.size, C.byref(err)), err, "set vector")
                    self.lib.check(L.CBLIndexUpdater_Finish(up, C.byref(err)), err, "finish")
                    return int(n)
                finally:
                    L.CBL_Release(up)
            finally:
                L.CBL_Release(ix)

    # ---- queries -------------------------------------------------------------
    def _compiled(self, sql: str):
        q = self._q.get(sql)
        if q is None:
            err, pos = CBLError(), C.c_int()
            q = self._q[sql] = self.lib.check(
                self.lib.L.CBLDatabase_CreateQuery(self.h, N1QL, S(sql), C.byref(pos), C.byref(err)),
                err, f"query (at {pos.value})")
        return q

    def iter_query(self, sql: str, params: dict | None = None):
        """Rows as dicts, streamed (the lock held until the iteration ends --
        for the boot load; a caller that breaks early must close it)."""
        L = self.lib.L
        with self.lock:
            q = self._compiled(sql)
            doc = None
            if params is not None:
                ferr = C.c_int()
                doc = L.FLDoc_FromJSON(S(json.dumps(params)), C.byref(ferr))
                L.CBLQuery_SetParameters(q, L.FLValue_AsDict(L.FLDoc_GetRoot(doc)))
            err = CBLError()
            rs = self.lib.check(L.CBLQuery_Execute(q, C.byref(err)), err, "execute")
            try:
                while L.CBLResultSet_Next(rs):
                    yield json.loads(self.lib.take(L.FLValue_ToJSON(L.CBLResultSet_ResultDict(rs))))
            finally:
                L.CBL_Release(rs)
                if doc:
                    L.FLDoc_Release(doc)

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        return list(self.iter_query(sql, params))

    def close(self):
        with self.lock:
            err = CBLError()
            for q in self._q.values():
                self.lib.L.CBL_Release(q)
            self._q.clear()
            for h in self.colls.values():
                self.lib.L.CBL_Release(h)
            self.colls.clear()
            self.lib.L.CBLDatabase_Close(self.h, C.byref(err))
            self.lib.L.CBL_Release(self.h)
            self.h = None
