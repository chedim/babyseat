"""#980 Q3 rows 9-10: the goal-graph triggers ship as a versioned seed pack."""
import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import seedpack as SP  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


def _srv(vm=True):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, membrane=None)
    est._srv = srv
    if vm:
        srv.ch_vm = CH.VM(est)
    return srv


def _trigs(est):
    return {e: (d.cls, dict(d.properties)) for e, d in est.docs.items() if d.cls == CH.CLASS}


def test_pack_equals_what_vm_define_wrote():
    a, b = _srv(), _srv()
    assert GG.seed(a) == len(GG.TRIGGERS)
    for name, when, where, write in GG.TRIGGERS:
        b.ch_vm.define(name, when, where, write)
    ta, tb = _trigs(a.entity_store), _trigs(b.entity_store)
    assert ta == tb and len(ta) == len(GG.TRIGGERS)


def test_versioned_release_wins_and_edit_is_an_overlay():
    s = _srv()
    est = s.entity_store
    GG.seed(s)
    assert SP.installed_version(est, "goal-graph") == GG.PACK_VERSION
    eid = GG._trigger_eid(GG.TRIGGERS[0][0])
    est.docs[eid].properties["to"] = "edited"
    GG.seed(s)                                  # same version: a no-op, the edit stays
    assert est.docs[eid].properties["to"] == "edited"
    pk = GG.pack()
    pk["version"] += 1
    SP.load(est, pk, release=True)
    assert est.docs[eid].properties["to"] != "edited"
    assert SP.overlays(est, "goal-graph")[0]["properties"]["to"] == "edited"


def test_no_vm_seeds_nothing():
    assert GG.seed(_srv(vm=False)) == 0
