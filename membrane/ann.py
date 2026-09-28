"""THE ANN CLIENT (the E13/E14 measurement made architecture): the
database's native vector index answers nearest-neighbor queries at
~3ms flat -- the RAM matrix's 80-200ms matrix-rebuild per call
disappears, and the latency stays FLAT as the field grows. This module
is the proxy's only search-REST surface: one function, one shape.

THE WIRE (E13-pinned): POST <search-host>:8094/api/index/<index>/query
with {"top_n": k, "knn": [{"field": <vec field>, "vector": [...],
"k": k}]} -- the bleve knn shape (the k in the spec must match top_n's
k or the server pads with its own default). Auth: basic (the env
credentials). Every failure degrades to None -- the caller (the RAM
store) owns the fallback (the degraded-mode covenant).

THE PARITY COVENANT (E13): recall parity ANN-vs-RAM is measured (the
e13/e14 probes); the hot path switches on parity, never on faith.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request


def _search_host() -> str:
    return os.environ.get("MEMBRANE_SEARCH_HOST", "http://127.0.0.1:8094")


def ann_search(index: str, vec: list, k: int = 5,
               timeout: float = 1.0,
               host: str | None = None, field: str = "vec",
               filter: dict | None = None) -> list[tuple[str, float]] | None:
    """One ANN query -> [(doc_id, score)] (best first), or None on ANY
    failure (the caller falls back to the RAM store). The vec must be
    the FULL dimension the index was built at (short vecs return 0
    hits silently). ``field`` names the indexed vector field (the
    exemplars index uses 'vec', entity states 'state'); ``filter`` is a
    search query pre-filtering the candidates (e.g. {"term": "live",
    "field": "tier"})."""
    if not vec:
        return None
    host = host or _search_host()
    knn = {"field": field, "vector": list(vec), "k": int(k)}
    if filter:
        knn["filter"] = filter
    # size: the search API pages its hits (10 by default) whatever knn's k says
    body = json.dumps({"top_n": int(k), "size": int(k), "knn": [knn]}).encode()
    req = urllib.request.Request(
        f"{host}/api/index/{index}/query",
        data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": _basic_auth()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
    except Exception:
        return None                     # degrade: the RAM store answers
    hits = d.get("hits") or []
    return [(str(h.get("id")), float(h.get("score") or 0.0)) for h in hits]


def _basic_auth() -> str:
    user = os.environ.get("COUCHBASE_USER", "Administrator")
    pw = os.environ.get("COUCHBASE_PASSWORD", "password")
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def vector_index_def(name: str, bucket: str, scope: str, collection: str,
                     field: str, dims: int,
                     keyword_fields: tuple[str, ...] = ()) -> dict:
    """The search-index definition for one vector field (+ keyword fields
    usable as knn filters), in the server's JSON shape."""
    props = {field: {"enabled": True, "dynamic": False, "fields": [{
        "name": field, "type": "vector", "dims": int(dims),
        "similarity": "dot_product", "index": True}]}}
    for kf in keyword_fields:
        props[kf] = {"enabled": True, "dynamic": False, "fields": [{
            "name": kf, "type": "text", "analyzer": "keyword",
            "index": True}]}
    return {
        "name": name, "type": "fulltext-index", "sourceType": "gocbcore",
        "sourceName": bucket,
        "params": {"doc_config": {"mode": "scope.collection.type_field",
                                  "type_field": "type"},
                   "mapping": {"default_mapping": {"enabled": False},
                               "types": {f"{scope}.{collection}": {
                                   "enabled": True, "dynamic": False,
                                   "properties": props}}}},
        "planParams": {"indexPartitions": 1},
    }


def ensure_vector_index(scope_handle, name: str, bucket: str, scope: str,
                        collection: str, field: str, dims: int,
                        keyword_fields: tuple[str, ...] = ()) -> bool:
    """Create-or-update a vector index through the SDK's scope search
    index manager (works where the :8094 REST surface is closed, e.g.
    Capella). False on any failure: recall then stays on the RAM path
    (the degraded-mode covenant)."""
    try:
        from couchbase.management.search import SearchIndex
        d = vector_index_def(name, bucket, scope, collection, field, dims,
                             keyword_fields)
        scope_handle.search_indexes().upsert_index(SearchIndex.from_json(d))
        return True
    except Exception as e:
        # an upsert without the live index's UUID is refused as a create:
        # the index is there, and that is what the caller needs to know
        return "already exists" in str(e) or "AlreadyExists" in type(e).__name__
