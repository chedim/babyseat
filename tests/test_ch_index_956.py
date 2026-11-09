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
    if hasattr(vm, "trace"):                    # the native VM (#971): its core reports the triggers visited
        vm.trace = []
        return vm.trace
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


def _scenario(vm, steps, index_on=True):
    """Run steps on a toy and return whether the mixed trigger fired, index on or off."""
    if not index_on:
        vm._relevant = lambda prop: vm.triggers()
    out = []
    for s in steps:
        s(vm)
        vm.drain()
    return out


def _mixed_run(index_on, steps):
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    v = CH.VM(est)
    v.attach()
    if not index_on:
        v._relevant = lambda prop, v=v: v.triggers()
    t = QuantumEntity(eid=MEMBRANE_NS + "toy:die", cls="toy", properties={"name": "die", "a": 1, "z": 0})
    t.position = [0.0, 0.0, 1.0]
    est.upsert(t)
    die = est.docs[t.eid]
    v.define("mix", when=[["changed", "?x.a"], ["pos", "?x", 0.1]], where=[["isa", "?x", "toy"]],
             write=["?x.fired", "1 + (x.fired or 0)"])
    v.sweep(); v.drain()
    for step in steps:
        if step == "move":
            die.position = [float(len(die.properties)), 0.0, 0.0]
        elif step == "a":
            die.properties["a"] = int(die.properties.get("a") or 0) + 1
        else:
            die.properties["z"] = int(die.properties.get("z") or 0) + 1
        est.upsert(die)
        v.drain()
    v.detach()
    return die.properties.get("fired")


@pytest.mark.parametrize("steps", [["z", "move", "a"], ["a", "move", "z", "a"]])
def test_a_changed_trigger_with_pos_keeps_todays_firings(steps):
    """Memo's review: `pos` / `around` are pulses that advance per-binding state when evaluated, and before
    the index every write evaluated them. A trigger mixing `changed` with one of them stays always-visited,
    so its firings are the same with and without the index."""
    assert _mixed_run(True, steps) == _mixed_run(False, steps)


def test_triggers_on_one_write_run_in_definition_order(vm):
    """Memo's review: a `changed` trigger defined before a level trigger reads the value before the
    level trigger writes it, as before the index."""
    s = _ent(vm.est, "s", cls="sensor", temp=10, note=0)
    vm.define("first_changed", when=[["changed", "?x.temp"]], where=[["isa", "?x", "sensor"]],
              write=["?x.seen", "x.note"])
    vm.define("then_level", when=[["above", "x.temp", 25]], where=[["isa", "?x", "sensor"]],
              write=["?x.note", "99"])
    vm.sweep(); vm.drain()
    seen = _visits(vm)
    s.properties["temp"] = 30
    vm.est.upsert(s)
    vm.drain()
    assert seen[:2] == ["first_changed", "then_level"]
    assert s.properties.get("seen") == 0                       # read before the level trigger wrote 99
