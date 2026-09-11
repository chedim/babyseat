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
               host: str | None = None) -> list[tuple[str, float]] | None:
    """One ANN query -> [(doc_id, score)] (best first), or None on ANY
    failure (the caller falls back to the RAM store). The vec must be
    the FULL dimension the index was built at (short vecs return 0
    hits silently)."""
    if not vec:
        return None
    host = host or _search_host()
    body = json.dumps({"top_n": int(k),
                       "knn": [{"field": "vec", "vector": list(vec),
                                "k": int(k)}]}).encode()
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