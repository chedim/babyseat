"""One-shot move for the entity field's collections.

  copy  cb -> cb   (docs keep their keys, so re-running is idempotent)
  dump  cb -> json:DIR (one <collection>.jsonl per collection:
        {"k": <key>, "d": <doc>} lines)

`--origin` (repeatable) limits the per-origin collections -- events, entity
states/deltas, relations, claims, annotations, origin stats; `sentences` and
`terms` are shared, content-addressed, and always copied. NOT a sync: the
target gains docs; nothing is deleted on either side; the source is never
modified.

Endpoint forms (--from / --to):
    cb:BUCKET                          cluster/creds from COUCHBASE_* env
    cb://user:pw@host[:port]/BUCKET    explicit (port: the KV port, default
                                       11210; the HTTP port is derived)
    cbs://user:pw@host[:port]/BUCKET   TLS (Capella's couchbases://)
    json:DIR                           dump target only (a directory)

The exemplar field's migration (field.json hot/cold tiers) retired with the
exemplar field; git history is the archive.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .efstore import COLLECTIONS, CouchbaseBackend
from .substrate import connect

# ---- CLI --------------------------------------------------------------------

def _parse_endpoint(spec: str) -> dict:
    spec = spec.strip()
    if spec.startswith("json:"):
        return {"kind": "json", "path": Path(spec[5:] or ".membrane/efield")}
    if spec.startswith("cb:"):
        conn, user, pw, bucket, _host = _creds_env()
        return {"kind": "cb", "conn": conn, "user": user, "pw": pw,
                "bucket": spec[3:] or bucket or "membrane"}
    if spec.startswith(("cb://", "cbs://")):
        # cb://  -> plaintext (KV 11210); cbs:// -> TLS (Capella's
        # couchbases:// scheme, KV 11207). Port may carry the scheme.
        m = re.match(r"(cb|cbs)://([^:@/]+)(?::([^@/]*))?@([^/:]+)(?::(\d+))?/(.+)",
                     spec)
        if not m:
            raise SystemExit(f"[membrane move] unparseable cb(s):// spec: "
                             f"{spec}")
        scheme, user, pw, host, port, bucket = m.groups()
        default_port = "11207" if scheme == "cbs" else "11210"
        scheme_full = "couchbases" if scheme == "cbs" else "couchbase"
        return {"kind": "cb", "conn": f"{scheme_full}://{host}:{port or default_port}",
                "user": user, "pw": pw or "", "bucket": bucket}
    raise SystemExit(f"[membrane move] endpoint must start with json:, cb:, "
                     f"cbs://, or cb:// -- got {spec!r}")


def _creds_env() -> tuple[str, str, str, str, str]:
    from .substrate import _creds, _host
    conn, user, pw, bucket = _creds()
    return conn, user, pw, bucket, _host(conn)


# ---- the copy ---------------------------------------------------------------

def copy_efield(src, dst, origins: set | None = None) -> dict:
    """Every collection from one backend to another (docs keep their keys, so
    re-running is idempotent; `origins` limits the per-origin collections --
    sentences and terms are shared, content-addressed, and always copied).
    Returns per-collection counts."""
    out = {}
    for coll in COLLECTIONS:
        rows = {}
        for k, d in src.scan(coll, None):
            if origins and coll not in ("sentences", "terms") and \
                    d.get("origin") not in origins:
                continue
            rows[k] = d
            if len(rows) >= 500:
                dst.upsert_multi(coll, rows)
                out[coll] = out.get(coll, 0) + len(rows)
                rows = {}
        if rows:
            dst.upsert_multi(coll, rows)
        out[coll] = out.get(coll, 0) + len(rows)
    return out


def dump_efield(src, path: Path, origins: set | None = None) -> dict:
    """The collections as <path>/<collection>.jsonl ({"k": key, "d": doc})."""
    path.mkdir(parents=True, exist_ok=True)
    out = {}
    for coll in COLLECTIONS:
        n = 0
        with (path / f"{coll}.jsonl").open("w") as fh:
            for k, d in src.scan(coll, None):
                if origins and coll not in ("sentences", "terms") and \
                        d.get("origin") not in origins:
                    continue
                fh.write(json.dumps({"k": k, "d": d}) + "\n")
                n += 1
        out[coll] = n
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="membrane move",
        description="move the entity field's collections (cb -> cb, or "
                    "cb -> json:DIR dumps)")
    ap.add_argument("--from", dest="src", required=True,
                    help="endpoint: 'cb:BUCKET' (env creds) | "
                         "'cb://user:pw@host[:kvport]/BUCKET'")
    ap.add_argument("--to", dest="dst", required=True,
                    help="same forms, plus 'json:DIR' (dump: one jsonl per "
                         "collection)")
    ap.add_argument("--origin", action="append", default=None,
                    help="only these origins' docs (repeatable; sentences "
                         "and terms are shared and always copied)")
    a = ap.parse_args()

    src, dst = _parse_endpoint(a.src), _parse_endpoint(a.dst)
    if src["kind"] != "cb":
        print("[membrane move] --from reads from a cb endpoint",
              file=sys.stderr)
        return 1
    if dst["kind"] == "cb" and \
            (src["conn"], src["bucket"]) == (dst["conn"], dst["bucket"]):
        print("[membrane move] refusing: source and target are the same "
              "cluster/bucket", file=sys.stderr)
        return 1
    origins = set(a.origin) if a.origin else None
    try:
        sb = CouchbaseBackend(connect(src["conn"], src["user"], src["pw"]),
                              src["bucket"])
    except Exception as e:
        print(f"[membrane move] source bucket open failed: {e}",
              file=sys.stderr)
        return 1
    if dst["kind"] == "json":
        counts = dump_efield(sb, dst["path"], origins)
    else:
        try:
            db = CouchbaseBackend(connect(dst["conn"], dst["user"], dst["pw"]),
                                  dst["bucket"])
        except Exception as e:
            print(f"[membrane move] target bucket open failed: {e}",
                  file=sys.stderr)
            return 1
        counts = copy_efield(sb, db, origins)
    print(f"[membrane move] entity field: {json.dumps(counts)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())