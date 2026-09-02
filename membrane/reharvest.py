"""One-shot entity re-harvest migration: bring the whole field's entity
tags into the current extractor's geometry.

WHY: the extractor evolves (bare snake_case symbols, the entity-management
exception, the files:membrane mis-parse fix) -- cells harvested BEFORE a
fix carry stale or missing tags. Until migrated, the entity index misses
cells that should be reachable, tracks under-count, and the story lane
starves exactly where the pre-fix capture forms sit (the 3-cell entity-
layer pool was the live exhibit).

WHAT: re-run extract_entities(situation, action) for every safe cell,
overwrite extra["entities"], stamp _reharvest. Idempotent: stamped cells
skip (a re-run or crash resumes clean). Save ONCE at the end. The
extraction is $0 (pure regex); the pass is bounded only by the store scan.

Run: `membrane reharvest` (local, seconds; --dry to preview).
"""

from __future__ import annotations

import sys

from .runtime import Membrane


def reharvest_field(field_path: str, dry: bool = False,
                    force: bool = False) -> tuple[int, int]:
    """Re-harvest entity tags for every safe cell with the CURRENT
    extractor. Returns (updated, skipped). Idempotent via the
    extra['_reharvest'] stamp; --force re-harvests everything. A tag-set
    UNCHANGED cell is skipped too (the stamp only lands when tags moved --
    a no-op pass costs nothing)."""
    from .entities import extract_entities
    from .reembed import store_save
    from .field import ExemplarStore

    mem_store = ExemplarStore.load(field_path)
    updated = skipped = 0
    total = len(mem_store.exemplars)
    for i, e in enumerate(mem_store.exemplars):
        if not e.safe:
            continue
        extra = dict(e.extra or {})
        if not force and extra.get("_reharvest"):
            skipped += 1
            continue
        try:
            tags = extract_entities(str(e.situation or ""),
                                    str(e.action or ""))
        except Exception as ex:
            print(f"[reharvest] cell #{i} skipped ({ex})", file=sys.stderr)
            continue
        old = extra.get("entities") or {}
        if tags != old:
            extra["entities"] = tags
            extra["_reharvest"] = 1
            e.extra = extra
            updated += 1
        else:
            extra["_reharvest"] = 1
            e.extra = extra
            skipped += 1
        if updated and updated % 500 == 0:
            print(f"[reharvest] {updated} cells re-tagged ({i + 1}/{total})",
                  file=sys.stderr)
    if not dry and updated:
        mem_store._written = set()          # force the full reconcile
        mem_store._written_eph = set()
        store_save(mem_store)
    print(f"[reharvest] done: {updated} cells re-tagged, {skipped} skipped "
          f"of {len(mem_store.exemplars)}"
          + (" (DRY -- nothing saved)" if dry else ""), file=sys.stderr)
    return updated, skipped


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        prog="membrane reharvest",
        description="one-shot: re-harvest entity tags over the whole field "
                    "with the CURRENT extractor (the entity-axis upgrade "
                    "instrument; the reembed analog for tags)")
    ap.add_argument("--field", default=".membrane/field.json")
    ap.add_argument("--dry", action="store_true",
                    help="report what WOULD change; save nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-harvest even cells already stamped")
    args = ap.parse_args()
    reharvest_field(args.field, dry=args.dry, force=args.force)


if __name__ == "__main__":
    main()