"""#956 stage 1: the VM indexes `changed` triggers by the property they watch. A write on
<eid>.<prop> visits only the `changed` triggers watching <prop>, plus every level trigger
(near, above, quiet... -- any write can change their bindings); `every` triggers are the
sweep's. The index is rebuilt only when a trigger is written. Firings are unchanged."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _ent(est, name, cls="toy", **props):
    e = QuantumEntity(eid=MEMBRANE_NS + f"{cls}:{name}", cls=cls, properties={"name": name, **props})
    est.upsert(e)
    return est.docs[e.eid]


@pytest.fixture
def vm():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    v = CH.VM(est)
    v.attach()
    yield v
    v.detach()


def _visits(vm):
    seen = []
    real = vm._on_write

    def spy(tr, ent, w, now, memo):
        seen.append(tr.properties["name"])
        return real(tr, ent, w, now, memo)
    vm._on_write = spy
    return seen


def test_a_write_visits_only_the_changed_triggers_on_its_property(vm):
    die = _ent(vm.est, "die", a=1, b=1)
    for i in range(30):
        vm.define(f"on_b_{i}", when=[["changed", "?x.b"]], where=[["isa", "?x", "toy"]], write=["?x.out", "1"])
    vm.define("on_a", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.got", "x.a"])
    vm.drain()
    seen = _visits(vm)
    die.properties["a"] = 7
    vm.est.upsert(die)
    vm.drain()
    assert seen == ["on_a"]                                    # not the 30 watching b
    assert die.properties["got"] == 7                          # and it still fires


def test_level_triggers_still_hear_every_write(vm):
    s = _ent(vm.est, "t", cls="sensor", temp=10)
    vm.define("hot", when=[["above", "x.temp", 25]], where=[["isa", "?x", "sensor"]], write=["?x.alarm", "1"])
    vm.define("on_other", when=[["changed", "?x.other"]], where=[["isa", "?x", "sensor"]], write=["?x.o", "1"])
    vm.sweep(); vm.drain()
    seen = _visits(vm)
    s.properties["temp"] = 30
    vm.est.upsert(s)
    vm.drain()
    assert "hot" in seen and "on_other" not in seen          # the level one hears it; the other prop does not
    assert s.properties.get("alarm") == 1


def test_the_index_follows_new_and_removed_triggers(vm):
    die = _ent(vm.est, "die", a=1)
    vm.drain()
    seen = _visits(vm)
    die.properties["a"] = 2; vm.est.upsert(die); vm.drain()
    assert seen == []
    t = vm.define("late", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.got", "x.a"])
    vm.drain()
    die.properties["a"] = 3; vm.est.upsert(die); vm.drain()
    assert seen == ["late"] and die.properties["got"] == 3
    vm.est.remove(t) if hasattr(vm.est, "remove") else vm.est.docs.pop(t)
    vm.drain()
    seen.clear()
    die.properties["a"] = 4; vm.est.upsert(die); vm.drain()
    assert seen == []


def test_a_literal_dotted_eid_is_indexed_by_its_property(vm):
    f = _ent(vm.est, "router.py", cls="f", a=1)                # membrane::f:router.py -- a dot in the eid
    vm.define("lit", when=[["changed", f"{f.eid}.a"]], where=[], write=[f"{f.eid}.b", "1"])
    vm.define("other", when=[["changed", f"{f.eid}.z"]], where=[], write=[f"{f.eid}.c", "1"])
    vm.drain()
    seen = _visits(vm)
    f.properties["a"] = 2; vm.est.upsert(f); vm.drain()
    assert seen == ["lit"] and f.properties.get("b") == 1
