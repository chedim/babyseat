"""#576 part B: the cliffhanger core. A trigger is an entity (a reified edge:
watched -> written) with WHEN watchers, WHERE pattern atoms and a WRITE. The
VM hears every field write (part A), evaluates the triggers OUTSIDE the
writer's call (a queue it drains), fires edge-triggered once per binding, and
writes through the store with the chain carried on -- refusing a chain that
comes back to a trigger unless it passed through self after it."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

OP = "membrane::operator"


def _store():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _ent(est, name, cls="Component", pos=None, **props):
    e = QuantumEntity(eid=MEMBRANE_NS + f"{cls}:{name}", cls=cls, properties={"name": name, **props})
    if pos is not None:
        e.position = list(pos)
    est.upsert(e)
    return est.docs[e.eid]


@pytest.fixture
def vm():
    est = _store()
    v = CH.VM(est)
    v.attach()
    yield v
    v.detach()


# ---- expressions ---------------------------------------------------------------------------

def test_expressions_have_vectors_and_builtins():
    env = {"a": [1.0, 0.0], "b": [0.0, 1.0], "n": 2}
    assert CH.evaluate("dot(a, b) + n * 3", env) == 6
    assert CH.evaluate("a + b", env) == [1.0, 1.0]
    assert round(CH.evaluate("distance(a, b)", env), 6) == 1.0
    assert CH.evaluate("norm(a) == 1 and n > 1", env) is True
    for bad in ("__import__('os')", "a.__class__", "(lambda: 1)()", "[x for x in a]"):
        with pytest.raises(CH.Refused):
            CH.evaluate(bad, env)


def test_an_entity_reads_by_attribute(vm):
    lamp = _ent(vm.est, "lamp", status="open", pos=[1.0, 0.0])
    env = {"x": CH.EntityView(vm.est, lamp.eid)}
    assert CH.evaluate("x.status == 'open'", env) is True
    assert CH.evaluate("x.position", env) == [1.0, 0.0]


# ---- defining ------------------------------------------------------------------------------

def test_a_definition_is_checked(vm):
    with pytest.raises(CH.Refused):
        vm.define("t", when=[["sing", "?x"]], where=[["isa", "?x", "toy"]], write=["?x.a", "1"])
    with pytest.raises(CH.Refused):                       # ?y is bound by nothing
        vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?y.b", "1"])
    with pytest.raises(CH.Refused):                       # one event watcher per trigger
        vm.define("t", when=[["changed", "?x.a"], ["every", 5]], where=[["isa", "?x", "toy"]],
                  write=["?x.b", "1"])
    t = vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x.a + 1"])
    doc = vm.est.docs[t]
    assert doc.cls == "Trigger" and CH.render(doc) == "WHEN changed(?x.a) WHERE isa(?x, toy): ?x.b = x.a + 1"


# ---- watchers ------------------------------------------------------------------------------

def test_changed_fires_once_per_write_outside_the_writer(vm):
    die = _ent(vm.est, "die", cls="toy", a=1)
    vm.define("inc", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x.a + 1"])
    die.properties["a"] = 5
    vm.est.upsert(die)
    assert "b" not in die.properties                     # the writer's call returned first
    vm.drain()
    assert die.properties["b"] == 6
    die.properties["other"] = 1
    vm.est.upsert(die)
    vm.drain()
    assert die.properties["b"] == 6                      # another path: nothing


def test_the_trigger_writes_in_the_chain_with_its_name(vm):
    die = _ent(vm.est, "die", cls="toy", a=1)
    t = vm.define("inc", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x.a + 1"])
    got = []
    W.on_write(got.append)
    try:
        with W.source("person", "op") as ch:
            die.properties["a"] = 2
            vm.est.upsert(die)
        vm.drain()
    finally:
        W.off_write(got.append)
    w = next(x for x in got if x["path"].endswith(".b"))
    assert w["writer"] == f"trigger:{t}" and w["chain"]["id"] == ch["id"] and t in w["chain"]["via"]


def test_isa_binds_only_its_class(vm):
    die = _ent(vm.est, "die", cls="toy", a=1)
    lamp = _ent(vm.est, "lamp", a=1)
    vm.define("inc", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "1"])
    lamp.properties["a"] = 2
    vm.est.upsert(lamp)
    vm.drain()
    assert "b" not in lamp.properties and "b" not in die.properties


def test_near_fires_on_the_crossing_once_per_binding(vm):
    op = _ent(vm.est, "op", pos=[1.0, 0.0])
    a = _ent(vm.est, "a", cls="toy", pos=[0.0, 1.0])
    b = _ent(vm.est, "b", cls="toy", pos=[0.0, 1.0])
    vm.define("close", when=[["near", "?x", op.eid, 0.2]], where=[["isa", "?x", "toy"]],
              write=["?x.close", "1 + (x.close or 0)"])
    vm.sweep()
    a.position = [1.0, 0.05]
    vm.est.upsert(a)
    vm.sweep(); vm.drain()
    vm.sweep(); vm.drain()                               # still near: no second fire
    assert a.properties.get("close") == 1 and "close" not in b.properties
    a.position = [0.0, 1.0]; vm.est.upsert(a); vm.sweep(); vm.drain()
    a.position = [1.0, 0.0]; vm.est.upsert(a); vm.sweep(); vm.drain()
    assert a.properties["close"] == 2                    # re-armed on the return


def test_above_crosses_up(vm):
    t = _ent(vm.est, "thermo", cls="sensor", temp=20)
    vm.define("hot", when=[["above", "x.temp", 25]], where=[["isa", "?x", "sensor"]],
              write=["?x.alarm", "1 + (x.alarm or 0)"])
    vm.sweep()
    t.properties["temp"] = 30; vm.est.upsert(t); vm.drain(); vm.sweep(); vm.drain()
    t.properties["temp"] = 31; vm.est.upsert(t); vm.drain(); vm.sweep(); vm.drain()
    assert t.properties["alarm"] == 1


def test_every_fires_per_binding_on_its_period(vm):
    s = _ent(vm.est, "door", cls="sensor", n=0)
    vm.define("tick", when=[["every", 60]], where=[["isa", "?x", "sensor"]], write=["?x.n", "x.n + 1"])
    for now in (1000, 1030, 1061):
        vm.sweep(now=now); vm.drain()
    assert s.properties["n"] == 2                        # at 1000 and at 1061


def test_quiet_fires_once_after_silence(vm):
    t = [1000.0]
    vm.clock = lambda: t[0]
    s = _ent(vm.est, "door", cls="sensor")
    vm.define("still", when=[["quiet", "?x", 300]], where=[["isa", "?x", "sensor"]],
              write=["?x.q", "1 + (x.q or 0)"])
    vm.drain()
    t[0] = 1200; vm.sweep(); vm.drain()
    assert "q" not in s.properties
    t[0] = 1301; vm.sweep(); vm.drain()                 # quiet 301 s: fires (and its write is a write)
    t[0] = 1500; vm.sweep(); vm.drain()                 # 199 s since: re-armed, not quiet yet
    assert s.properties["q"] == 1
    t[0] = 1700; vm.sweep(); vm.drain()                 # quiet again: fires again
    assert s.properties["q"] == 2


# ---- the loop rule -------------------------------------------------------------------------

def test_a_loop_is_refused_unless_it_passes_through_self(vm):
    x = _ent(vm.est, "x", cls="toy", a=0, b=0)
    ta = vm.define("a2b", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x.a + 1"])
    vm.define("b2a", when=[["changed", "?x.b"]], where=[["isa", "?x", "toy"]], write=["?x.a", "x.b + 1"])
    x.properties["a"] = 1; vm.est.upsert(x)
    vm.drain(max_n=50)
    assert (x.properties["a"], x.properties["b"]) == (3, 2)   # a=1 -> b=2 -> a=3 -> a2b refused
    assert vm.refused.get(ta) == 1


def test_a_loop_through_self_is_allowed(vm):
    x = _ent(vm.est, "x", cls="toy", a=0, b=0)
    vm.define("a2b", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x.a + 1"])
    vm.define("b2a", when=[["changed", "?x.b"]], where=[["isa", "?x", "toy"]], write=["?x.a", "x.b + 1"],
              owner=CH.SELF)
    x.properties["a"] = 1; vm.est.upsert(x)
    vm.drain(max_n=40)
    assert x.properties["a"] > 5                         # it keeps going (energy bounds it, part C)


def test_the_seat_runs_the_vm():
    import inspect
    from membrane import opencode, thinker
    assert "_CH.VM(srv.entity_store)" in inspect.getsource(opencode) and "srv.ch_vm.start()" in inspect.getsource(opencode)
    assert "_vm.sweep()" in inspect.getsource(thinker)


def test_the_worker_drains_on_its_own(vm):
    import time as _t
    die = _ent(vm.est, "die", cls="toy", a=1)
    vm.define("inc", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x.a + 1"])
    vm.start()
    die.properties["a"] = 9
    vm.est.upsert(die)
    for _ in range(50):
        if die.properties.get("b") == 10:
            break
        _t.sleep(0.02)
    assert die.properties.get("b") == 10
