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


class _Live:
    def on_fire(self, tr, path, value):
        return "live"

    def observe(self, w):
        pass


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
    t[0] = 1301; vm.sweep(); vm.drain()                 # quiet 301 s: fires
    t[0] = 1700; vm.sweep(); vm.drain()                 # its own write is not the door's activity: still quiet
    assert s.properties["q"] == 1
    t[0] = 1750
    with W.as_writer("person:op"):
        s.properties["opened"] = 1; vm.est.upsert(s)    # real activity re-arms it
    vm.drain(); vm.sweep(); vm.drain()
    t[0] = 2100; vm.sweep(); vm.drain()
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
    vm.learner = _Live()                                 # self's triggers run only with a learner (part C)
    x.properties["a"] = 1; vm.est.upsert(x)
    vm.drain(max_n=40)
    assert x.properties["a"] > 5                         # it keeps going (energy bounds it, part C)


def test_the_seat_runs_the_vm():
    import inspect
    from membrane import opencode, thinker
    assert "_CH.VM(srv.entity_store" in inspect.getsource(opencode) and "srv.ch_vm.start()" in inspect.getsource(opencode)
    assert "_vm.request_sweep()" in inspect.getsource(thinker)


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
    vm.stop()
    assert die.properties.get("b") == 10


# ---- #578 review ---------------------------------------------------------------------------

def test_an_entity_is_never_written_into_the_field(vm):
    import json
    die = _ent(vm.est, "die", cls="toy", a=1)
    t = vm.define("leak", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "x"])
    die.properties["a"] = 2; vm.est.upsert(die); vm.drain()
    assert "b" not in die.properties and "cannot write" in vm.errors[t]
    json.dumps(die.properties)


def test_arithmetic_is_pythons_and_refuses_the_undefined():
    env = {"n": 10_000_000_000}
    assert CH.evaluate("n * n", env) == 10 ** 20
    for bad in ("1 / 0", "True + True", "None + 1"):
        with pytest.raises(CH.Refused):
            CH.evaluate(bad, env)
    with pytest.raises(CH.Refused):
        CH.evaluate("+".join(["1"] * 200), {})


def test_one_bad_trigger_does_not_starve_the_sweep(vm):
    s = _ent(vm.est, "t", cls="sensor", temp=20)
    bad = vm.define("bad", when=[["above", "x.temp + x.missing", 5]], where=[["isa", "?x", "sensor"]],
                    write=["?x.z", "1"])
    vm.define("good", when=[["above", "x.temp", 25]], where=[["isa", "?x", "sensor"]], write=["?x.alarm", "1"])
    vm.sweep()
    s.properties["temp"] = 30
    vm.sweep()
    assert s.properties.get("alarm") == 1 and bad in vm.errors


def test_a_level_trigger_hears_a_write_without_a_sweep(vm):
    s = _ent(vm.est, "t", cls="sensor", temp=20)
    vm.define("hot", when=[["above", "x.temp", 25]], where=[["isa", "?x", "sensor"]], write=["?x.alarm", "1"])
    vm.sweep()                                           # seeds
    s.properties["temp"] = 30; vm.est.upsert(s); vm.drain()
    assert s.properties.get("alarm") == 1


def test_changed_on_a_literal_eid_and_a_dotted_eid(vm):
    f = _ent(vm.est, "router.py", cls="f", a=1)          # membrane::f:router.py -- a dot in the eid
    vm.define("lit", when=[["changed", f"{f.eid}.a"]], where=[], write=[f"{f.eid}.b", "1"])
    vm.define("var", when=[["changed", "?x.a"]], where=[["isa", "?x", "f"]], write=["?x.c", "x.a"])
    f.properties["a"] = 2; vm.est.upsert(f); vm.drain()
    assert f.properties.get("b") == 1 and f.properties.get("c") == 2


def test_a_new_vm_does_not_refire_what_is_already_true(vm):
    s = _ent(vm.est, "t", cls="sensor", temp=30)
    vm.define("hot", when=[["above", "x.temp", 25]], where=[["isa", "?x", "sensor"]],
              write=["?x.alarm", "1 + (x.alarm or 0)"])
    vm.sweep(); vm.drain()
    vm2 = CH.VM(vm.est)
    vm2.sweep(); vm2.drain()
    assert s.properties.get("alarm") is None


def test_code_and_definitions_are_not_a_triggers_to_write(vm):
    with pytest.raises(CH.Refused):
        vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.command", "'rm'"])
    with pytest.raises(CH.Refused):
        vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.tick_script", "'x'"])
    other = vm.define("o", when=[["every", 60]], where=[["isa", "?x", "toy"]], write=["?x.n", "1"])
    t = vm.define("t", when=[["every", 60]], where=[["is", "?y", other]], write=["?y.name", "'x'"])
    vm.sweep(); vm.drain()
    assert "no target" in vm.errors[t]                   # a trigger is never a trigger's target


def test_selfs_triggers_are_inert_without_a_learner(vm):
    x = _ent(vm.est, "x", cls="toy", a=0)
    vm.define("mine", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "1"], owner=CH.SELF)
    x.properties["a"] = 1; vm.est.upsert(x); vm.drain()
    assert "b" not in x.properties


def test_a_relay_chain_cannot_claim_self():
    with W.from_headers({W.HDR_CHAIN: '{"id": "c", "root": "x", "via": ["t1", "self"]}'}):
        assert W.current()["via"] == ["t1"]


def test_a_sweep_gives_each_firing_its_own_chain(vm):
    a, b = _ent(vm.est, "a", cls="toy", n=0), _ent(vm.est, "b", cls="toy", n=0)
    vm.define("tick", when=[["every", 60]], where=[["isa", "?x", "toy"]], write=["?x.n", "1"])
    got = []
    W.on_write(got.append)
    try:
        vm.sweep()
    finally:
        W.off_write(got.append)
    ids = [w["chain"]["id"] for w in got if w["path"].endswith(".n")]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_nothing_runs_while_paused():
    est = _store()
    paused = [True]
    v = CH.VM(est, paused=lambda: paused[0])
    v.attach()
    try:
        die = _ent(est, "die", cls="toy", a=1)
        v.define("inc", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.b", "1"])
        die.properties["a"] = 2; est.upsert(die)
        v.drain()
        assert "b" not in die.properties
        paused[0] = False
        v.drain()
        assert die.properties["b"] == 1
    finally:
        v.detach()


def test_below_away_pos_filter_and_a_position_write(vm):
    op = _ent(vm.est, "op", pos=[1.0, 0.0])
    s = _ent(vm.est, "s", cls="toy", temp=20, pos=[1.0, 0.0])
    vm.define("cold", when=[["below", "x.temp", 10]], where=[["isa", "?x", "toy"]], write=["?x.cold", "1"])
    vm.define("far", when=[["away", "?x", op.eid, 0.5]], where=[["isa", "?x", "toy"], ["filter", "x.temp < 100"]],
              write=["?x.far", "1"])
    vm.define("moved", when=[["pos", "?x", 0.5]], where=[["isa", "?x", "toy"]], write=["?x.moved", "1"])
    vm.define("home", when=[["changed", "?x.go"]], where=[["is", "?x", s.eid]], write=["?x.position", "[1.0, 0.0]"])
    vm.define("bad", when=[["changed", "?x.go"]], where=[["is", "?x", s.eid]], write=["?x.position", "[1.0]"])
    vm.sweep()
    s.properties["temp"] = 5
    s.position = [-1.0, 0.0]
    vm.sweep()
    assert (s.properties.get("cold"), s.properties.get("far"), s.properties.get("moved")) == (1, 1, 1)
    s.properties["go"] = 1; vm.est.upsert(s); vm.drain()
    assert list(s.position) == [1.0, 0.0]                # the 2-d write landed; the 1-d one was refused


# ---- #578 re-review: self's ownership cannot be forged ---------------------------------------

def test_a_forged_self_trigger_falls_under_the_loop_rule(vm):
    vm.learner = _Live()
    x = _ent(vm.est, "x", cls="toy", a=0)
    forged = QuantumEntity(eid=MEMBRANE_NS + "Trigger:forged", cls="Trigger",
                           properties={"name": "forged", "owner": CH.SELF, "when": [["changed", "?x.a"]],
                                       "where": [["isa", "?x", "toy"]], "write": ["?x.a", "x.a + 1"]})
    vm.est.upsert(forged)
    x.properties["a"] = 1; vm.est.upsert(x)
    vm.drain(max_n=60)
    assert x.properties["a"] == 2 and vm.refused.get(forged.eid) == 1     # one hop, then the rule


def test_a_defined_self_trigger_is_signed_and_loops(vm):
    vm.learner = _Live()
    x = _ent(vm.est, "x", cls="toy", a=0)
    t = vm.define("mine", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.a", "x.a + 1"],
                  owner=CH.SELF)
    assert vm.est.docs[t].properties.get("owner_sig")
    x.properties["a"] = 1; vm.est.upsert(x)
    vm.drain(max_n=20)
    assert x.properties["a"] > 3


def test_the_signature_survives_a_new_vm_on_the_same_store(vm):
    t = vm.define("mine", when=[["every", 60]], where=[["isa", "?x", "toy"]], write=["?x.n", "1"], owner=CH.SELF)
    vm2 = CH.VM(vm.est)
    assert vm2._is_selfs(vm.est.docs[t]) and vm._is_selfs(vm.est.docs[t])


def test_a_trigger_is_never_edited_through_the_entity_api(vm):
    t = vm.define("t", when=[["every", 60]], where=[["isa", "?x", "toy"]], write=["?x.n", "1"])
    res = vm.est.edit(t, properties={"owner": CH.SELF})
    assert not res.get("ok") and vm.est.docs[t].properties.get("owner") == ""


def test_an_imported_trigger_never_runs(vm):
    x = _ent(vm.est, "x", cls="toy", a=0)
    guest = QuantumEntity(eid="entity::peer::Trigger:theirs", cls="Trigger",
                          properties={"name": "theirs", "when": [["changed", "?x.a"]],
                                      "where": [["isa", "?x", "toy"]], "write": ["?x.b", "1"]})
    vm.est.upsert(guest)
    x.properties["a"] = 1; vm.est.upsert(x); vm.drain()
    assert "b" not in x.properties


def test_vel_align_and_around(vm):
    import types
    a = _ent(vm.est, "a", cls="toy", pos=[1.0, 0.0])
    b = _ent(vm.est, "b", cls="toy", pos=[0.0, 1.0])
    speed = {"v": 0.1}
    a.ring_velocity = lambda now: speed["v"]
    coh = {"c": 0.95}
    vm.est._srv = types.SimpleNamespace(efield=types.SimpleNamespace(
        get=lambda fid: types.SimpleNamespace(coherence=coh["c"])))
    vm.define("fast", when=[["vel", "?x", 0.5]], where=[["is", "?x", a.eid]], write=["?x.fast", "1"])
    vm.define("loose", when=[["align", "?x", 0.9]], where=[["is", "?x", a.eid]], write=["?x.loose", "1"])
    vm.define("crowd", when=[["around", "?x", 0.2]], where=[["is", "?x", a.eid]], write=["?x.crowd", "1"])
    vm.sweep(); vm.sweep()                               # seed (around needs two looks)
    speed["v"], coh["c"] = 0.9, 0.5
    b.position = [1.0, 0.05]
    vm.sweep()
    assert (a.properties.get("fast"), a.properties.get("loose"), a.properties.get("crowd")) == (1, 1, 1)
