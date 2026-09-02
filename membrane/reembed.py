"""One-shot re-embed migration: bring the whole field into the time-aware
embedding geometry.

WHY: cells embedded before the time-prefix change carry vectors from bare
text; new cells embed from `[2026-09-01T21:34Z] + text`. Until migrated,
old cells sit at a slight offset — dedup across eras misses, and the
temporal geometry only applies to new cells.

WHAT: re-embed `time_prefix + situation` for every safe cell, overwrite the
stored vec, save once at the end (couchbase delta or JSON). Idempotent:
cells already carrying a `_reembed` stamp in extra are skipped, so a
re-run or a crash mid-way resumes clean.

Run: `membrane reembed` (local mxbai, ~6.5k cells, 10-15 min).
"""

from __future__ import annotations

import sys
import time

from .runtime import Membrane


def _time_prefix() -> str:
    """The same universal timestamp the gate embeds with (runtime's shape)."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return f"[{now.strftime('%Y-%m-%dT%H:%M')}Z] "


def reembed_field(field_path: str, backend: str = "http://localhost:11434",
                  embed_model: str = "mxbai-embed-large",
                  batch: int = 32, force: bool = False) -> int:
    """Re-embed every safe cell with the time prefix. Returns the count
    migrated. Skips cells already stamped (idempotent); --force re-stamps
    everything. Saves ONCE at the end (atomic: one reconcile)."""
    from .embed import OllamaEmbedder
    from .field import ExemplarStore, Exemplar

    mem_store = ExemplarStore.load(field_path)
    emb = OllamaEmbedder(base_url=backend.rstrip("/"), model=embed_model)
    prefix = _time_prefix()
    n = 0
    total = len(mem_store.exemplars)
    for i, e in enumerate(mem_store.exemplars):
        if not e.safe:
            continue
        if not force and (e.extra or {}).get("_reembed"):
            continue
        try:
            vec = emb.embed([prefix + e.situation])[0]
            e.vec = [float(x) for x in vec]
            e.extra = dict(e.extra or {})
            e.extra["_reembed"] = 1
            n += 1
        except Exception as ex:
            print(f"[reembed] cell #{i} skipped ({ex})", file=sys.stderr)
        if n % 500 == 0 and n:
            print(f"[reembed] {n} cells re-embedded ({i + 1}/{total})",
                  file=sys.stderr)
    mem_store._written = set()          # force the full reconcile on save
    mem_store._written_eph = set()
    store_save(mem_store)
    print(f"[reembed] done: {n} cells re-embedded of {total}",
          file=sys.stderr)
    return n


def store_save(store) -> None:
    """The store's own save (couchbase delta or JSON), one call. The load
    bound the field path at construction (couchbase when MEMBRANE_STORE is
    set; the JSON fallback path otherwise)."""
    store.save(getattr(store, "_field_path_hint", "")
               or ".membrane/field.json")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        prog="membrane reembed",
        description="one-shot: re-embed the whole field with time-aware "
                    "geometry (universal UTC prefix)")
    ap.add_argument("--field", default=".membrane/field.json")
    ap.add_argument("--backend", default="http://localhost:11434")
    ap.add_argument("--embed-model", default="mxbai-embed-large")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--force", action="store_true",
                    help="re-embed even cells already stamped")
    args = ap.parse_args()
    reembed_field(args.field, args.backend, args.embed_model,
                  batch=args.batch, force=args.force)


if __name__ == "__main__":
    main()