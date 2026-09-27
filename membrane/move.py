"""One-shot field migration between stores: cb<->cb, cb->json, json->cb.

Reads EVERYTHING from the source -- every origin's patch (the veil does not
apply to a migration; the operator asked for everything) plus the cold-tier
archive -- and writes it into the target. Rows keep their origins, so a moved
together patch stays scenery on arrival (read `--reorigin` for the fork-the-
identity move). Content-keyed (origin + situation + action), so re-running is
idempotent: present rows re-upsert in place, nothing duplicates.

Endpoint forms (--from / --to):
    json:PATH                          a field.json (hot tier; the cold tier
                                       rides alongside as PATH.archive.jsonl)
    cb:BUCKET                          cluster/creds from COUCHBASE_* env
    cb://user:pw@host[:port]/BUCKET    explicit (port: the KV port, default
                                       11210; the HTTP port is derived)

Both ends go through the production wire code (_rec_from_wire / _ex_to_wire /
_ex_key); a cb target is written by a real `load_couchbase` store's
`save()` -- reconcile, sit-channel arbitration, and the fresh-bucket
bootstrap are the same code paths a boot takes. NOT a sync: the target gains
a snapshot; nothing is deleted on either side. The source is never modified.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from .field import (_CB_COLL, _CB_SCOPE, ExemplarStore, _ex_key,
                    _ex_to_wire, _origin_tag, _rec_from_wire)

_TIMEOUT = timedelta(seconds=60)


# ---- source readers ---------------------------------------------------------

def _connect(conn: str, user: str, pw: str):
    """A ready cluster handle (the field.py cred convention).

    TLS connstrings (couchbases:// -- Capella and secured clusters) are
    passed through; the SDK needs a root CA for Capella's public cert,
    provided via COUCHBASE_CERT_PATH (or the well-known homebrew curl
    bundle on macOS)."""
    from couchbase.auth import PasswordAuthenticator
    from couchbase.cluster import Cluster
    from couchbase.options import ClusterOptions

    opts = {}
    cert = os.environ.get("COUCHBASE_CERT_PATH", "").strip()
    if not cert and conn.startswith("couchbases"):
        for cand in ("/etc/ssl/cert.pem",
                     "/opt/homebrew/etc/ca-certificates/cert.pem",
                     "/usr/local/etc/openssl@3/cert.pem"):
            if os.path.isfile(cand):
                cert = cand
                break
    if cert:
        opts["cert_path"] = cert
    cluster = Cluster(conn, ClusterOptions(PasswordAuthenticator(user, pw,
                                                                 **opts)))
    cluster.wait_until_ready(timedelta(seconds=30))
    return cluster


def read_cb_all(conn: str, user: str, pw: str, bucket: str,
                archive: bool = False) -> list[dict]:
    """Every exemplar doc in the source collection, INCLUDING foreign
    patches. `archive=True` reads the cold tier instead. sit: channel docs
    (arbitration bookkeeping) are excluded -- the target rebuilds its own."""
    from couchbase.options import QueryOptions

    cluster = _connect(conn, user, pw)
    coll = _CB_COLL + ("_archive" if archive else "")
    qr = cluster.query(
        "SELECT meta().id AS id, t.* "
        f"FROM `{bucket}`.{_CB_SCOPE}.`{coll}` t "
        "WHERE meta().id NOT LIKE 'sit:%' "
        "AND meta().id NOT LIKE 'mem:%'",
        QueryOptions(timeout=_TIMEOUT))
    rows = []
    for r in qr:
        rec = {k: v for k, v in r.items() if k != "id"}
        rec["_origin"] = rec.get("_origin") or rec.get("origin") or ""
        rows.append(rec)
    return rows


def read_json_all(path: Path) -> tuple[list[dict], list[dict]]:
    """A json endpoint: the field file (hot) plus its sibling
    `PATH.archive.jsonl` (the cold tier, when present)."""
    hot = []
    if path.exists() and path.read_text().strip():
        hot = list(json.loads(path.read_text()))
    cold = []
    apath = Path(str(path) + ".archive.jsonl")
    if apath.exists():
        with open(apath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cold.append(json.loads(line))
                except json.JSONDecodeError:
                    continue    # a torn tail line is not fatal (store rule)
    return hot, cold


# ---- reassembly -------------------------------------------------------------

def _decode(rows: list[dict]) -> list:
    """Wire rows -> Exemplars (schema-window aware; blank origins stay blank
    until a writer stamps them). A row that fails to decode or carries no
    situation AND no action is bookkeeping, not a memory -- skipped with a
    count, never fatal (a migration must survive one weird doc)."""
    out = []
    skipped = 0
    for rec in rows:
        try:
            ex = _rec_from_wire(rec)
        except Exception:
            skipped += 1
            continue
        if not ex.origin and rec.get("_origin"):
            ex.origin = rec["_origin"]
        if ex.situation or ex.action:
            out.append(ex)
        else:
            skipped += 1
    if skipped:
        print(f"[membrane move] skipped {skipped} non-exemplar/malformed "
              f"doc(s)", file=sys.stderr)
    return out


# ---- writers ----------------------------------------------------------------

def write_json(hot: list, cold: list, path: Path,
               origin: str) -> tuple[int, int]:
    """Hot rows -> the field file (v1 shape); cold rows -> the sibling
    .archive.jsonl (the stock cold-tier file, `_key`-keyed, idempotent on
    rerun). Blank origins are stamped with `origin`; sourced origins are
    kept. Returns (hot_written, cold_written)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str]] = set()
    recs = []
    for ex in hot:
        if not ex.origin:
            ex.origin = origin
        key = (ex.situation, ex.origin)
        if key in seen:
            continue
        seen.add(key)
        recs.append(_ex_to_wire(ex, stamp=False))
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                               dir=str(path.parent))
    with os.fdopen(fd, "w") as f:
        json.dump(recs, f, indent=2)
    os.replace(tmp, path)

    apath = Path(str(path) + ".archive.jsonl")
    n_cold = 0
    seen_cold: set[str] = set()
    if apath.exists():
        with open(apath) as f:
            for line in f:
                try:
                    seen_cold.add(json.loads(line).get("_key", ""))
                except json.JSONDecodeError:
                    continue
    with open(apath, "a") as f:
        import time
        for ex in cold:
            if not ex.origin:
                ex.origin = origin
            k = _ex_key(ex)
            if k in seen_cold:
                continue
            rec = _ex_to_wire(ex, stamp=False)
            rec["_archived_at"] = rec.get("_archived_at") \
                if isinstance(rec.get("_archived_at"), str) \
                else time.strftime("%Y-%m-%dT%H:%M:%S")
            rec["_key"] = k
            f.write(json.dumps(rec) + "\n")
            seen_cold.add(k)
            n_cold += 1
    return len(recs), n_cold


def write_cb(hot: list, cold: list, field_path: Path, origin: str,
             reorigin: bool) -> tuple[int, int, int]:
    """Hot + cold -> the target bucket (the env already points there). The
    store is hydrated with the REAL field path -- that loads the bucket's
    own+global patch first, so (a) dedup sees what the bucket already holds
    and (b) the save()'s stale-key reconcile can never delete a doc the move
    didn't carry (hydrate-then-append keeps `live` a superset of `_cb_keys`).

    Own-origin rows go through `save()` (reconcile + sit-channel arbitration,
    the production paths). Foreign-origin rows -- kept when !reorigin -- are
    written by DIRECT upsert (origin-namespaced `_ex_key`, no sit-channel:
    scenery, it must not contend), because `_save_couchbase` never writes
    foreign-origin rows.
    Returns (hot_new, hot_dups, cold_new)."""
    target = ExemplarStore.load_couchbase(field_path)
    n = dups = 0
    foreign: list = []
    for ex in hot:
        if reorigin or not ex.origin:
            ex.origin = origin
        if ex.origin == target.origin:
            if any(e.situation == ex.situation and e.origin == ex.origin
                   for e in target.exemplars):
                dups += 1
                continue
            target.append(ex)
            n += 1
        else:
            foreign.append(ex)
    if n:
        target.save(field_path)     # _cb set -> _save_couchbase
    for ex in foreign:
        k = _ex_key(ex)
        target._cb.upsert(k, target._cb_doc(ex))
    cold_new = target.archive_exemplars(cold, field_path)
    return n, dups, cold_new


# ---- CLI --------------------------------------------------------------------

def _parse_endpoint(spec: str) -> dict:
    spec = spec.strip()
    if spec.startswith("json:"):
        return {"kind": "json", "path": Path(spec[5:] or ".membrane/field.json")}
    if spec.startswith("cb:"):
        return {"kind": "cb",
                "conn": os.environ.get("COUCHBASE_CONN_STRING",
                                       "couchbase://127.0.0.1"),
                "user": os.environ.get("COUCHBASE_USER", "Administrator"),
                "pw": os.environ.get("COUCHBASE_PASSWORD", "password"),
                "bucket": spec[3:] or "membrane"}
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
        return {"kind": "cb",
                "conn": f"{scheme_full}://{host}:{port or default_port}",
                "user": user, "pw": pw or "", "bucket": bucket}
    raise SystemExit(f"[membrane move] endpoint must start with json:, cb:, "
                     f"cbs://, or cb:// -- got {spec!r}")


# ---- the entity field's collections ---------------------------------------------
def copy_efield(src, dst, origins: set | None = None) -> dict:
    """Every entity-field collection from one backend to another (docs keep
    their keys, so re-running is idempotent; `origins` limits the per-origin
    collections -- sentences and terms are shared, content-addressed, and
    always copied). Returns per-collection counts."""
    from .efstore import COLLECTIONS
    out = {}
    for coll in COLLECTIONS:
        rows = {}
        for k, d in src.scan(coll, None):
            if origins and coll not in ("sentences", "terms") and d.get("origin") not in origins:
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
    from .efstore import COLLECTIONS
    path.mkdir(parents=True, exist_ok=True)
    out = {}
    for coll in COLLECTIONS:
        n = 0
        with (path / f"{coll}.jsonl").open("w") as fh:
            for k, d in src.scan(coll, None):
                if origins and coll not in ("sentences", "terms") and d.get("origin") not in origins:
                    continue
                fh.write(json.dumps({"k": k, "d": d}) + "\n")
                n += 1
        out[coll] = n
    return out


def _efield_main(a, src: dict, dst: dict) -> int:
    from .efstore import CouchbaseBackend
    if src["kind"] != "cb":
        print("[membrane move] --efield reads from a cb endpoint", file=sys.stderr)
        return 1
    sb = CouchbaseBackend(_connect(src["conn"], src["user"], src["pw"]), src["bucket"])
    origins = set(a.origin) if a.origin else None
    if dst["kind"] == "json":
        counts = dump_efield(sb, dst["path"], origins)
    else:
        db = CouchbaseBackend(_connect(dst["conn"], dst["user"], dst["pw"]), dst["bucket"])
        counts = copy_efield(sb, db, origins)
    print(f"[membrane move] entity field: {json.dumps(counts)}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="membrane move",
        description="move a field between stores (cb<->cb, cb->json, json->cb)")
    ap.add_argument("--from", dest="src", required=True,
                    help="endpoint: 'json:PATH' | 'cb:BUCKET' (env creds) | "
                         "'cb://user:pw@host[:kvport]/BUCKET'")
    ap.add_argument("--to", dest="dst", required=True, help="same forms")
    ap.add_argument("--field", default=".membrane/field.json",
                    help="JSON field path used for json endpoints and for "
                         "the persisted origin tag (when neither endpoint "
                         "is json:)")
    ap.add_argument("--reorigin", action="store_true",
                    help="re-stamp EVERY row with the target project's "
                         "origin (default: keep source origins; only blanks "
                         "are stamped -- a normal move keeps the source's "
                         "patches readable as scenery)")
    ap.add_argument("--efield", action="store_true",
                    help="move the ENTITY FIELD's collections (events, sentences, "
                         "terms, entity states/deltas, relations, claims, "
                         "annotations, origin stats) instead of exemplars: "
                         "cb -> cb, or cb -> json:DIR (one .jsonl per collection)")
    ap.add_argument("--origin", action="append", default=None,
                    help="--efield: only these origins' docs (repeatable; "
                         "sentences and terms are shared and always copied)")
    a = ap.parse_args()

    src, dst = _parse_endpoint(a.src), _parse_endpoint(a.dst)
    if src["kind"] == "cb" and dst["kind"] == "cb" and \
            (src["conn"], src["bucket"]) == (dst["conn"], dst["bucket"]):
        print("[membrane move] refusing: source and target are the same "
              "cluster/bucket", file=sys.stderr)
        return 1
    if a.efield:
        return _efield_main(a, src, dst)

    # the origin tag for stamping: the json endpoint's identity if one is a
    # json: endpoint, else the --field project's persisted tag
    json_path = (src["path"] if src["kind"] == "json"
                 else dst["path"] if dst["kind"] == "json"
                 else Path(a.field))
    origin = _origin_tag(json_path)

    def _read(ep: dict) -> tuple[list, list]:
        if ep["kind"] == "json":
            hot, cold = read_json_all(ep["path"])
            return _decode(hot), _decode(cold)
        hot = _decode(read_cb_all(ep["conn"], ep["user"], ep["pw"],
                                  ep["bucket"]))
        cold = _decode(read_cb_all(ep["conn"], ep["user"], ep["pw"],
                                   ep["bucket"], archive=True))
        return hot, cold

    hot, cold = _read(src)
    print(f"[membrane move] source: {len(hot)} hot + {len(cold)} archived "
          f"exemplars; stamping blanks with origin {origin!r}"
          + (" (and re-stamping ALL rows)" if a.reorigin else ""),
          file=sys.stderr)

    if dst["kind"] == "json":
        n_hot, n_cold = write_json(hot, cold, dst["path"], origin)
        print(f"[membrane move] {dst['path']}: {n_hot} hot exemplars, "
              f"{n_cold} archived -> {dst['path']}.archive.jsonl",
              file=sys.stderr)
        return 0

    os.environ["COUCHBASE_CONN_STRING"] = dst["conn"]
    os.environ["COUCHBASE_USER"] = dst["user"]
    os.environ["COUCHBASE_PASSWORD"] = dst["pw"]
    os.environ["COUCHBASE_BUCKET"] = dst["bucket"]
    n_hot, hot_dups, n_cold = write_cb(hot, cold, json_path, origin,
                                       reorigin=a.reorigin)
    print(f"[membrane move] target bucket {dst['bucket']!r}: +{n_hot} hot "
          f"({hot_dups} already present), +{n_cold} archived", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())