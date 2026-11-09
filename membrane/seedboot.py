"""#980 Q3 k3: boot every seed pack (idempotent, version-aware; a deleted entity is re-created)."""
import sys


def boot_packs(est) -> dict:
    from . import dials, graphbanner, recall_policy, seedpack, seedscripts, seedtables
    packs = [("banner", graphbanner.pack), ("dials", lambda: dials.PACK),
             ("recall-policy", lambda: recall_policy.PACK), ("scripts-rows-12-15", seedscripts.pack),
             ("seedtables", seedtables.build_pack)]
    out = {}
    for name, get in packs:
        try:
            out[name] = seedpack.load(est, get(), release=True)
        except Exception as ex:                     # one bad pack must not stop the boot
            print(f"[seedboot] pack {name} skipped ({ex})", file=sys.stderr)
    try:
        recall_policy.install(est)
    except Exception as ex:
        print(f"[seedboot] recall_policy.install skipped ({ex})", file=sys.stderr)
    return out
