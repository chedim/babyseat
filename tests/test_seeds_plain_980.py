"""#980 Q3 rows 1-8: parity -- the plain seed pack makes the same entities/properties as the seeders."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import autonode, fstree, language, night, seedpack  # noqa: E402
from membrane import seeds_plain as SPL  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _est():
    return EntityStore(path=None, shared=False)


def _snap(est, skip=()):
    return {e: (d.cls, {k: v for k, v in d.properties.items()})
            for e, d in est.docs.items() if not e.startswith("membrane::seedpack:") and e not in skip}


def _legacy(est):
    night.seed_clock_entity(est)
    night.seed_executor_entity(est)
    autonode.seed_filetree_entity(est)
    fstree.seed_classes(est)
    language.seed(est)
    language.seed_executor(est)


def test_parity_with_legacy_seeders():
    a, b = _est(), _est()
    _legacy(a)
    SPL.boot(b)
    sa, sb = _snap(a), _snap(b)
    assert set(sa) == set(sb) and len(sa) >= 12
    for eid in sa:
        assert sa[eid][0] == sb[eid][0], eid
        assert sa[eid][1] == sb[eid][1], eid


def test_second_boot_is_noop():
    est = _est()
    SPL.boot(est)
    assert SPL.boot(est)["installed"] == 0
    assert seedpack.installed_version(est, "plain") == SPL.PACK_VERSION


def test_existing_edited_clock_survives_first_adoption():
    est = _est()
    night.seed_clock_entity(est)
    clk = est.docs[night.MEMBRANE_NS + "tool:clock"]
    clk.properties["tick_script"] = "result = {}"
    SPL.boot(est)
    assert clk.properties["tick_script"] == "result = {}"
    assert night.MEMBRANE_NS + "tool:executor" in est.docs
