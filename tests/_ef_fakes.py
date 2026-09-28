"""Shared fakes for the entity-field tests.

FakeEmbedder: deterministic across processes (sha256-seeded -- never
Python's salted hash(), the flaky-test lesson) and additive like mxbai
(a text embeds as the normalized sum of its word vectors), so sentences
sharing words are similar and a bare term embeds near its sentences."""

from __future__ import annotations

import hashlib
import re

import numpy as np

_WORD = re.compile(r"[a-z0-9_./-]+")


def word_vec(word: str, dim: int = 64) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(word.encode()).digest()[:8], "big")
    v = np.random.default_rng(seed).standard_normal(dim)
    return v / np.linalg.norm(v)


class FakeEmbedder:
    model = "fake-embed"

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.calls = 0

    def embed(self, texts):
        out = []
        for t in texts:
            self.calls += 1
            words = _WORD.findall((t or "").lower()) or ["<empty>"]
            v = np.sum([word_vec(w, self.dim) for w in words], axis=0)
            out.append(v / (np.linalg.norm(v) + 1e-12))
        return out


# ---- a fake Couchbase cluster: the SDK surface CouchbaseBackend uses -------------
import copy as _copy  # noqa: E402


class _Content:
    def __init__(self, doc):
        self._doc = doc

    def __getitem__(self, _t):
        return _copy.deepcopy(self._doc)


class _GetResult:
    def __init__(self, doc, cas):
        self.content_as = _Content(doc)
        self.cas = cas


class _Multi:
    def __init__(self, results):
        self.results = results


class FakeCollection:
    def __init__(self):
        self.docs: dict = {}
        self.cas: dict = {}
        self._n = 0
        self.before_replace = None           # test hook: simulate a racing writer

    def _bump(self, key):
        self._n += 1
        self.cas[key] = self._n

    def get(self, key):
        from couchbase.exceptions import DocumentNotFoundException
        if key not in self.docs:
            raise DocumentNotFoundException()
        return _GetResult(self.docs[key], self.cas[key])

    def get_multi(self, keys):
        return _Multi({k: _GetResult(self.docs[k], self.cas[k]) for k in keys if k in self.docs})

    def upsert(self, key, doc):
        self.docs[key] = _copy.deepcopy(doc)
        self._bump(key)

    def upsert_multi(self, docs):
        for k, d in docs.items():
            self.upsert(k, d)

    def insert(self, key, doc):
        from couchbase.exceptions import DocumentExistsException
        if key in self.docs:
            raise DocumentExistsException()
        self.upsert(key, doc)

    def replace(self, key, doc, opts=None):
        from couchbase.exceptions import CasMismatchException, DocumentNotFoundException
        if self.before_replace:
            hook, self.before_replace = self.before_replace, None
            hook(self)
        if key not in self.docs:
            raise DocumentNotFoundException()
        if opts is not None and opts.get("cas") not in (None, self.cas[key]):
            raise CasMismatchException()
        self.upsert(key, doc)

    def remove(self, key):
        from couchbase.exceptions import DocumentNotFoundException
        if key not in self.docs:
            raise DocumentNotFoundException()
        del self.docs[key]


class _CollMgr:
    def __init__(self, cluster):
        self.cl = cluster

    def create_scope(self, scope):
        if scope in self.cl.scopes:
            raise RuntimeError("scope exists")
        self.cl.scopes[scope] = {}

    def create_collection(self, scope, coll):
        if coll in self.cl.scopes.setdefault(scope, {}):
            raise RuntimeError("collection exists")
        self.cl.scopes[scope][coll] = FakeCollection()


class _Scope:
    def __init__(self, cluster, name):
        self.cl, self.name = cluster, name

    def collection(self, coll):
        return self.cl.scopes.setdefault(self.name, {}).setdefault(coll, FakeCollection())


class _Bucket:
    def __init__(self, cluster):
        self.cl = cluster

    def scope(self, name):
        return _Scope(self.cl, name)

    def collections(self):
        return _CollMgr(self.cl)


_SELECT = re.compile(r"FROM `[^`]+`\.`([^`]+)`\.`([^`]+)` t(?: WHERE (.*?))?"
                     r"(?: ORDER BY (.*?))?(?: LIMIT (\d+))?$")


class _Lazy:
    def __init__(self, fn):
        self.fn = fn

    def __iter__(self):
        return iter(self.fn())


class FakeCluster:
    """Runs exactly the SQL++ efstore.scan_sql emits; CREATE is a no-op."""

    def __init__(self):
        self.scopes: dict = {}
        self.statements: list = []

    def bucket(self, _name):
        return _Bucket(self)

    def query(self, sql, opts=None):
        """Lazy, like the SDK's: nothing runs until the result is iterated
        (an un-iterated CREATE INDEX never reaches the server)."""
        return _Lazy(lambda: self._execute(sql, opts))

    def _execute(self, sql, opts=None):
        self.statements.append(sql)
        if not sql.startswith("SELECT"):
            return []
        m = _SELECT.search(sql)
        scope, coll, where, order, limit = m.groups()
        params = (opts or {}).get("named_parameters") or {}
        conds = []
        for part in (where.split(" AND ") if where else []):
            f = re.search(r"t\.`([^`]+)`", part).group(1)
            p = re.search(r"\$(\w+)", part).group(1)
            op = re.search(r"` (IN|=|>=|<=|>|<) \$", part).group(1)
            conds.append((f, op, params[p]))
        docs = self.scopes.get(scope, {}).get(coll, FakeCollection()).docs

        def ok(got, op, v):
            if op == "IN":
                return got in v
            if op == "=":
                return got == v
            return got is not None and {">": got > v, ">=": got >= v,
                                        "<": got < v, "<=": got <= v}[op]
        rows = [(k, d) for k, d in docs.items()
                if all(ok(d.get(f), op, v) for f, op, v in conds)]
        if order:
            fields = re.findall(r"t\.`([^`]+)`", order)
            rows.sort(key=lambda kd: tuple(kd[1].get(f, 0) for f in fields))
        if limit:
            rows = rows[:int(limit)]
        return [{"k": k, "d": _copy.deepcopy(d)} for k, d in rows]
