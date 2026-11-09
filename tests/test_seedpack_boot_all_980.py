"""#980 Q3 k2: every seed pack is loaded at boot (membrane.seedboot.boot_packs, called from the seat boot).
Per pack: a fresh field gets it; an entity deleted from an existing field at the same version comes back."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from membrane import seedpack  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _packs():
    from membrane import dials, graphbanner, recall_policy, seedscripts, seedtables
    return {"banner": graphbanner.pack(), "dials": dials.PACK, "recall-policy": recall_policy.PACK,
            "scripts-rows-12-15": seedscripts.pack(), "seedtables": seedtables.build_pack()}


NAMES = ["banner", "dials", "recall-policy", "scripts-rows-12-15", "seedtables"]


def _est():
    return EntityStore(path=None, shared=False)


def _boot(est):
    from membrane import seedboot
    return seedboot.boot_packs(est)


@pytest.mark.parametrize("name", NAMES)
def test_fresh_field_installs_pack_at_boot(name):
    p = _packs()[name]
    est = _est()
    _boot(est)
    assert seedpack.installed_version(est, name) == int(p["version"])
    assert all(e["eid"] in est.docs for e in p["entities"])


@pytest.mark.parametrize("name", NAMES)
def test_deleted_entity_rematerializes_at_same_version(name):
    p = _packs()[name]
    est = _est()
    _boot(est)
    eid = p["entities"][0]["eid"]
    del est.docs[eid]
    assert seedpack.installed_version(est, name) == int(p["version"])
    _boot(est)
    assert eid in est.docs
