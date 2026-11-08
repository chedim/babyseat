"""#956 stage 2: a call is a write. A request `{id, args}` written to `<obj>.call:<m>` reaches the method
`resolve_method` picks for that object through its dispatch trigger
(`WHERE dispatches(?x, <m>, <method eid>)`, a real binder: exactly one method's trigger matches, today's
own -> classes -> global order), which writes `<method>.invoke = {object, call, request}`; the `invoke`
adapter runs THAT method (no second lookup by name) and leaves the receipt, with the request's id, on the
object: `<obj>.call:<m>_result` / `_rc`. A changed trigger reads the written value as `new` (and the one it
replaced as `old`), so two quick calls never overwrite each other's request. Secret-named args are refused
on a written call (they can't be both redacted and used). Method names with '.', or ending in '_result' /
'_rc', are refused at define (they would collide with paths and receipts)."""
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import adapters as A  # noqa: E402
from membrane import ch as CH  # noqa: E402
from membrane import night  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _store():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _method(est, owner, name, command):
    assert night.method_define(None, est, None, None, owner, name, command)["ok"]
    return next(d for d in est.docs.values() if (d.properties or {}).get("method_name") == name
                and owner in ((d.properties or {}).get("method_of") or [owner]))


def _dispatch(vm, m, name):
    """The dispatch trigger stage 5's migration will define for each method."""
    return vm.define(f"dispatch {m.eid}", when=[["changed", f"?x.call:{name}"]],
                     where=[["dispatches", "?x", name, m.eid]],
                     write=[f"{m.eid}.invoke", f"{{'object': x.eid, 'call': 'call:{name}', 'request': new}}"])


@pytest.fixture
def world():
    est = _store()
    srv = types.SimpleNamespace(entity_store=est, efield=None, membrane=None)
    vm = CH.VM(est)
    vm.attach()
    A.start(srv)
    yield est, vm
    A.stop()
    vm.detach()


def _run(est, vm, obj, prop, value):
    with W.source("person", "op"), W.as_writer("llm"):
        obj.properties[prop] = value
        est.upsert(obj)
    for _ in range(3):
        vm.drain()
        A.drain()


def test_a_written_request_runs_the_method_and_leaves_its_receipt_on_the_object(world):
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "hello", "echo hi-{{who}}")
    _dispatch(vm, m, "hello")
    vm.drain()
    _run(est, vm, g, "call:hello", {"id": "r1", "args": {"who": "bob"}})
    rec = g.properties.get("call:hello_result")
    assert isinstance(rec, dict) and rec["id"] == "r1" and rec["rc"] == 0 and "hi-bob" in rec["head"]
    assert g.properties.get("call:hello_rc") == 0


def test_dispatches_matches_exactly_the_method_resolve_method_picks(world):
    est, vm = world
    lamp = QuantumEntity(eid=MEMBRANE_NS + "Component:lamp", cls="Component", properties={"name": "lamp"})
    est.upsert(lamp)
    a = QuantumEntity(eid=MEMBRANE_NS + "dev:a", cls="Component", properties={"name": "a", "parents": [lamp.eid]})
    b = QuantumEntity(eid=MEMBRANE_NS + "dev:b", cls="Component", properties={"name": "b", "parents": [lamp.eid]})
    est.upsert(a); est.upsert(b)
    a, b = est.docs[a.eid], est.docs[b.eid]
    cls_m = _method(est, lamp.eid, "on", "echo class-on")
    own_m = _method(est, a.eid, "on", "echo own-on")
    assert night.resolve_method(est, a, "on")[0].eid == own_m.eid
    assert night.resolve_method(est, b, "on")[0].eid == cls_m.eid
    _dispatch(vm, cls_m, "on"); _dispatch(vm, own_m, "on")
    vm.drain()
    _run(est, vm, a, "call:on", {"id": "x1", "args": {}})
    _run(est, vm, b, "call:on", {"id": "x2", "args": {}})
    assert "own-on" in a.properties["call:on_result"]["head"]      # the object's own method, not the class's
    assert "class-on" in b.properties["call:on_result"]["head"]
    assert cls_m.properties.get("runs") == 1 and own_m.properties.get("runs") == 1


def test_a_changed_trigger_reads_the_written_value_as_new_and_the_replaced_one_as_old(world):
    est, vm = world
    t = QuantumEntity(eid=MEMBRANE_NS + "toy:die", cls="toy", properties={"name": "die", "v": 1})
    est.upsert(t)
    t = est.docs[t.eid]
    vm.define("seen", when=[["changed", "?x.v"]], where=[["isa", "?x", "toy"]], write=["?x.log", "[old, new]"])
    vm.drain()
    t.properties["v"] = 5; est.upsert(t)
    t.properties["v"] = 9; est.upsert(t)                       # two writes before the VM drains
    vm.drain()
    assert t.properties["log"] == [5, 9]                       # the second firing read its own write


def test_prop_reads_a_property_whose_name_is_not_an_identifier(world):
    est, vm = world
    t = QuantumEntity(eid=MEMBRANE_NS + "toy:die", cls="toy", properties={"name": "die", "call:x": 3})
    est.upsert(t)
    assert CH.evaluate("prop(x, 'call:x') + 1", {"x": CH.EntityView(est, t.eid)}) == 4


def test_an_empty_or_malformed_request_runs_nothing(world):
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "hello", "echo hi")
    _dispatch(vm, m, "hello")
    vm.drain()
    for bad in ("", {}, {"args": {}}, "hello", {"id": "", "args": {}}):
        _run(est, vm, g, "call:hello", bad)
    assert not m.properties.get("runs")
    assert "call:hello_result" not in g.properties or g.properties["call:hello_result"].get("rc") != 0


def test_secret_named_args_are_refused_on_a_written_call(world):
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "login", "echo {{api_token}}")
    _dispatch(vm, m, "login")
    vm.drain()
    _run(est, vm, g, "call:login", {"id": "s1", "args": {"api_token": "abc123"}})
    rec = g.properties["call:login_result"]
    assert rec["id"] == "s1" and rec["rc"] == 2 and "secret" in rec["head"]
    assert not m.properties.get("runs")
    assert "abc123" not in str(g.properties.get("call:login_result"))
    # Memo's review of 3c8f84f: the refused secret is stored nowhere, and the invoke slot is consumed
    assert "abc123" not in str(g.properties.get("call:login"))
    assert "abc123" not in str(m.properties.get("invoke")) and not m.properties.get("invoke")


def test_a_secret_nested_inside_an_arg_is_refused_and_scrubbed(world):
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "login", "echo {{conf}}")
    _dispatch(vm, m, "login")
    vm.drain()
    _run(est, vm, g, "call:login", {"id": "s2", "args": {"conf": {"user": "bob", "password": "hunter2"}}})
    rec = g.properties["call:login_result"]
    assert rec["id"] == "s2" and rec["rc"] == 2 and "conf.password" in rec["head"]
    assert not m.properties.get("runs")
    assert "hunter2" not in str(g.properties) and "hunter2" not in str(m.properties)


@pytest.mark.parametrize("req", [
    {"id": "s3", "args": {"conf": {"x": {"y": {"z": {"password": "hunter2"}}}}}},   # too deep to write
    {"args": {"password": "hunter2"}},                                              # malformed: no id
    {"id": "s4", "args": [{"password": "hunter2"}]},                                # malformed: args not a dict
])
def test_a_request_refused_before_the_secret_check_is_scrubbed_too(world, req):
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "go", "echo ok")
    _dispatch(vm, m, "go")
    vm.drain()
    _run(est, vm, g, "call:go", req)
    assert g.properties["call:go_result"]["rc"] == 2, vm.errors
    assert "hunter2" not in repr(g.properties) and "hunter2" not in repr(m.properties)


def test_a_call_with_an_object_argument_runs_or_gets_a_receipt(world):
    """Memo's review: `args: {conf: {a: 1}}` used up the write depth (invoke -> request -> args) and
    vanished into vm.errors -- no run, no receipt. The envelope doesn't count against the args' depth."""
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "go", "echo {{conf}} {{nums}}")
    _dispatch(vm, m, "go")
    vm.drain()
    _run(est, vm, g, "call:go", {"id": "d1", "args": {"conf": {"a": 1}, "nums": [1, 2]}})
    rec = g.properties.get("call:go_result")
    assert isinstance(rec, dict) and rec["id"] == "d1" and rec["rc"] == 0, (rec, vm.errors)
    assert "1.0" not in rec["head"]                          # integers in lists stay integers
    # too deep for the args' own limit: refused WITH a receipt, never silently
    deep = {"a": {"b": {"c": {"d": 1}}}}
    _run(est, vm, g, "call:go", {"id": "d2", "args": {"conf": deep}})
    rec = g.properties.get("call:go_result")
    assert isinstance(rec, dict) and rec["id"] == "d2" and rec["rc"] == 2, (rec, vm.errors)


def test_clearing_a_call_path_keeps_the_last_receipt(world):
    est, vm = world
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:greeter", cls="Component", properties={"name": "greeter"})
    est.upsert(g)
    g = est.docs[g.eid]
    m = _method(est, g.eid, "go", "echo ok")
    _dispatch(vm, m, "go")
    vm.drain()
    _run(est, vm, g, "call:go", {"id": "e1", "args": {}})
    assert g.properties["call:go_result"]["rc"] == 0
    _run(est, vm, g, "call:go", "")
    assert g.properties["call:go_result"]["id"] == "e1" and g.properties["call:go_result"]["rc"] == 0
    assert not m.properties.get("invoke")


@pytest.mark.parametrize("bad", ["a.b", "x_result", "x_rc"])
def test_method_names_that_collide_with_paths_or_receipts_are_refused(bad):
    est = _store()
    g = QuantumEntity(eid=MEMBRANE_NS + "Component:g", cls="Component", properties={"name": "g"})
    est.upsert(g)
    r = night.method_define(None, est, None, None, g.eid, bad, "echo x")
    assert not r["ok"] and "name" in (r.get("error") or r.get("text") or "")
