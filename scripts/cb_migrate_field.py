#!/usr/bin/env python
"""One-off migration: seed the Couchbase field from the local JSON field.

Reads the JSON store at --field (default .membrane/field.json), connects to
Couchbase via load_couchbase (env: COUCHBASE_CONN_STRING / COUCHBASE_USER /
COUCHBASE_PASSWORD / COUCHBASE_BUCKET, defaults couchbase://localhost /
Administrator / password / membrane -- bucket fallback + scope/collection
bootstrap ride the stock machinery), stamps every exemplar with the JSON
field's persisted origin tag (membrane/field.py `_origin_tag` semantics), and
runs one `_save_couchbase` reconcile pass: per-doc upserts with
origin-namespaced content keys, situation-index docs for arbitration.

TWO TIERS: stable field data lands in the canon (on-disk/membase) bucket;
rows the JSON flagged `ephemeral: true` (candidates) are routed by the save
machinery into the dedicated ephemeral bucket (`COUCHBASE_EPHEMERAL_BUCKET`,
resolved `<canon>-ephemeral` -> any *ephemeral* bucket -> created; falls back
to the canon bucket's sibling `exemplars_ephemeral` collection) with a
server-side doc TTL -- the clock GCs them, the migration never promotes.

Idempotent: re-running re-upserts the same deterministic keys. The JSON field
is left untouched (this is a migration, not a move); delete it or the cold
tier yourself once the CB-backed seat is proven. Run with the PROJECT venv
(needs the couchbase SDK): `.venv/bin/python scripts/cb_migrate_field.py`.

After it, boots auto-detect the local cluster (field.py `load` probe) and
hydrate from Couchbase -- the JSON file becomes a cold backup of pre-migration
state.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.field import DATA_DIRNAME, EPH_COLL, EPH_TTL_S
from membrane.field import ExemplarStore, _eph_key, _ex_key, _situ_key
from membrane.field import _origin_tag, _rec_from_wire


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--field", default=".membrane/field.json",
                    help="path to the JSON field to migrate from")
    a = ap.parse_args()

    p = Path(a.field)
    if not p.exists():
        print(f"no JSON field at {p}; nothing to migrate", file=sys.stderr)
        return 1
    text = p.read_text()
    if not text.strip():
        print(f"{p} is empty; nothing to migrate", file=sys.stderr)
        return 1
    recs = json.loads(text)
    print(f"source: {p} ({len(recs)} exemplars)", file=sys.stderr)

    origin = _origin_tag(p)          # persisted identity (or derive + persist)
    print(f"origin tag: {origin}", file=sys.stderr)

    store = ExemplarStore()
    store.origin = origin
    for rec in recs:
        ex = _rec_from_wire(rec)
        if not ex.origin:
            ex.origin = origin       # pin blank/legacy rows to this identity
        store.append(ex)
    print(f"decoded {len(store.exemplars)} exemplars "
          f"({sum(1 for e in store.exemplars if e.safe)} safe)",
          file=sys.stderr)

    cb = ExemplarStore.load_couchbase(p)   # connects + bootstraps infra
    eph_home = cb._resolve_eph_bucket() or (
        f"{cb._cb_bucket} (sibling {EPH_COLL} collection)")
    print(f"couchbase ready: canon bucket {cb._cb_bucket!r}, ephemeral "
          f"tier -> {eph_home!r}; {len(cb)} exemplars already in the CB "
          f"patch (own+global only; foreign patches ride the veil)",
          file=sys.stderr)

    cb.exemplars = store.exemplars          # adopt the decoded patch
    cb._cb_situ = {_situ_key(e.situation) for e in cb.exemplars
                   if e.origin == origin}   # own situations: skip re-arbitrate
    cb.save(p)                              # _save_couchbase: upserts+arbitrate
    keys = {_ex_key(e) for e in cb.exemplars if e.origin == origin}
    got = sum(1 for k in keys if _doc_exists(cb, k))
    n_eph = sum(1 for e in cb.exemplars if e.ephemeral)
    print(f"upserted; verified {got}/{len(keys)} own docs present "
          f"under key prefix {origin} in the canon bucket", file=sys.stderr)
    if n_eph:
        eph_keys = {_eph_key(e) for e in cb.exemplars if e.ephemeral}
        eph_coll = cb._ensure_eph_collection()
        got_eph = sum(1 for k in eph_keys if _doc_exists_handle(eph_coll, k))
        print(f"ephemeral tier: {n_eph} candidate(s) verified "
              f"{got_eph}/{len(eph_keys)} in {cb._cb_eph_bucket or cb._cb_bucket!r} "
              f"(doc TTL {EPH_TTL_S:.0f}s); promotion stays evidence-gated",
              file=sys.stderr)
    print("done -- the JSON field is untouched (cold backup). A membrane "
          "booted with this cluster answering now hydrates from Couchbase.",
          file=sys.stderr)
    return 0


def _doc_exists(store: ExemplarStore, key: str) -> bool:
    from couchbase.exceptions import DocumentNotFoundException
    try:
        store._cb.get(key)
        return True
    except DocumentNotFoundException:
        return False


def _doc_exists_handle(coll: Any, key: str) -> bool:
    from couchbase.exceptions import DocumentNotFoundException
    try:
        coll.get(key)
        return True
    except DocumentNotFoundException:
        return False


if __name__ == "__main__":
    raise SystemExit(main())