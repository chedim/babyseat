"""#971 step 3 (red first): the native VM passes the same contract as ch.VM.
- the replay contract (test_journal_971): a journal recorded with ch.VM, replayed through the NATIVE
  VM, gives the same trigger writes in the same order and the same final state;
- ch.py's own VM tests, run with ch.VM swapped for the native one (tests/test_ch_native_suites_971.py)."""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

NV = pytest.importorskip("membrane.ch_native")
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import journal as J  # noqa: E402
from membrane import membrane_vm as MV  # noqa: E402
import test_journal_971 as TJ  # noqa: E402


def test_a_journal_recorded_with_ch_vm_replays_identically_through_the_native_vm():
    p = Path(tempfile.mkdtemp()) / "w.jsonl"
    est = TJ._session(p)
    fresh = TJ._store()
    rep = J.replay_vm(p, fresh, lambda s: NV.NativeVM(s))
    assert rep["mismatches"] == [], rep["mismatches"][:5]
    assert rep["vm_writes"] > 0
    assert TJ._state(fresh) == TJ._state(est)


@pytest.mark.parametrize("src,env,want", [
    ("new > 25", {"new": 27}, True),
    ("1 + 2 * 3", {}, 7),
    ("7 / 2", {}, 3.5),
    ("'a' + 'b'", {}, "ab"),
    ("[1, 2] + [3, 4]", {}, [4.0, 6.0]),
    ("[1, 2] * 2", {}, [2.0, 4.0]),
    ("x in [1, 2, 3]", {"x": 2}, True),
    ("x not in [1, 2]", {"x": 3}, True),
    ("1 < 'a'", {}, False),
    ("0 or 'z'", {}, "z"),
    ("1 and 0", {}, 0),
    ("not []", {}, True),
    ("max(3, 9, 4)", {}, 9),
    ("min([5, 2, 8])", {}, 2),
    ("abs(-2.5)", {}, 2.5),
    ("distance([1, 0], [0, 1])", {}, 1.0),
    ("norm([3, 4])", {}, 5.0),
    ("dot([1, 2], [3, 4])", {}, 11.0),
    ("unit([0, 2])", {}, [0.0, 1.0]),
    ("{'a': 1, 'b': [1, 2]}", {}, {"a": 1, "b": [1, 2]}),
    ("True == 1", {}, True),
    ("1 < 2 < 3", {}, True),
    ("-x", {"x": 4}, -4),
])
def test_the_expression_language_matches_ch_py(src, env, want):
    import json
    from membrane import ch as CH
    try:
        py = CH.evaluate(src, dict(env))
    except Exception as e:                       # noqa: BLE001
        py = ("refused", type(e).__name__)
    try:
        nat = json.loads(MV.evaluate(src, json.dumps(env)))
    except Exception as e:                       # noqa: BLE001
        nat = ("refused", type(e).__name__)
    if isinstance(py, tuple):
        assert isinstance(nat, tuple), (src, py, nat)
    else:
        assert nat == py, (src, py, nat)
        assert type(nat) is type(py) or isinstance(py, (list, dict)), (src, py, nat)


@pytest.mark.parametrize("src", ["x[0]", "lambda: 1", "__import__('os')", "f(x=1)",
                                 "x ** 2", "x // 2", "{1, 2}"])
def test_what_ch_py_refuses_the_native_parser_refuses(src):
    import json
    from membrane import ch as CH
    with pytest.raises(Exception):
        CH._check(CH._parse(src), {"x", "a", "b", "c"})
    with pytest.raises(Exception):
        MV.evaluate(src, json.dumps({"x": [1], "a": 1, "b": 1, "c": 1}))


def test_native_parser_takes_a_bare_if():
    """2026-11-10 (the operator): the native language has `a if c else b` (the Python VM, unsupported, still
    refuses it): getter arms compile to it."""
    import json
    assert json.loads(MV.evaluate("'on' if x > 0 else 'off'", json.dumps({"x": 2}))) == "on"
    assert json.loads(MV.evaluate("'on' if x > 0 else 'off'", json.dumps({"x": 0}))) == "off"


def test_native_parser_takes_a_list_comprehension():
    """#994 getters (`open_blockers`) compute over other entities with `[x for x in xs if c]`: the native parser
    takes it on purpose, ahead of ch.py, which still refuses it."""
    import json
    assert json.loads(MV.evaluate("[i for i in x if i > 1]", json.dumps({"x": [1, 2, 3]}))) == [2, 3]


# ---- Memo's review of 6f9b4de ------------------------------------------------------------------

def _toy_store():
    from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    est.upsert(QuantumEntity(eid=MEMBRANE_NS + "toy:die", cls="toy", properties={"name": "die", "a": 0}), embedder=None)
    return est, est.docs[MEMBRANE_NS + "toy:die"]


def test_reading_the_counters_while_another_thread_drains_never_deadlocks():
    """1: `fired` read off the worker thread while `step` was inside a host callback froze the process."""
    import threading
    import time
    from membrane import writes as W
    est, die = _toy_store()
    vm = NV.NativeVM(est)
    vm.attach()
    slow = vm.core  # noqa: F841
    real = NV._Host.resolve_exact
    NV._Host.resolve_exact = lambda self, ref: (time.sleep(0.002), real(self, ref))[1]
    try:
        vm.define("inc", when=[["changed", "?x.a"]], where=[["is", "?x", "toy:die"]], write=["?x.b", "x.a + 1"])
        stop = time.time() + 2.0
        done = {"n": 0}

        def writer():
            i = 0
            while time.time() < stop:
                i += 1
                with W.as_writer("person:t"):
                    die.properties["a"] = i
                    est.upsert(die, embedder=None)
                vm.drain()
            done["n"] = i

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        reads = 0
        while t.is_alive() and time.time() < stop + 5:
            _ = vm.fired, vm.errors, vm.report()
            reads += 1
        t.join(timeout=8)
        assert not t.is_alive(), "the drain thread is stuck (deadlock)"
        assert reads > 0 and done["n"] > 0
    finally:
        NV._Host.resolve_exact = real
        vm.detach()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "\ud800x", {"a": [[[[[[[[1]]]]]]]]}])
def test_a_value_strict_json_cannot_carry_never_stops_the_vm(bad):
    """2: NaN / Infinity / a lone surrogate / deep nesting in the store or in a write."""
    from membrane import ch as CH
    from membrane import writes as W
    est, die = _toy_store()
    die.properties["weird"] = bad
    est.upsert(die, embedder=None)
    deep = bad
    for _ in range(200):
        deep = [deep] if not isinstance(bad, float) else deep
    die.properties["deeper"] = deep
    est.upsert(die, embedder=None)
    vm = NV.NativeVM(est)                      # construction survives
    vm.attach()
    vm.define("inc", when=[["changed", "?x.a"]], where=[["is", "?x", "toy:die"]], write=["?x.b", "x.a + 1"])
    with W.as_writer("person:t"):
        die.properties["weird2"] = bad         # a write of it survives too
        die.properties["a"] = 4
        est.upsert(die, embedder=None)
    vm.drain()
    vm.detach()
    assert die.properties.get("b") == 5



def test_a_sweep_without_level_triggers_reads_nothing_from_the_store():
    """3: every sweep serialised the whole store; with no trigger a sweep can bind, it reads nothing."""
    est, _ = _toy_store()
    vm = NV.NativeVM(est)
    seen = []
    real = NV.NativeVM._refresh_rows
    NV.NativeVM._refresh_rows = lambda self, eids, positions=True: (seen.append(len(eids)), real(self, eids, positions))[1]
    try:
        vm.sweep()
        assert seen in ([], [0])
        vm.define("hot", when=[["above", "x.a", 5]], where=[["isa", "?x", "toy"]], write=["?x.hot", "1"])
        vm.sweep()
        assert seen[-1] == 1                   # only the one entity the level trigger can bind
    finally:
        NV.NativeVM._refresh_rows = real


def test_replay_covers_what_a_sweep_fires():
    """4: a journal with an `every` firing replayed as want 1, got 0 -- sweeps and the VM's clock are journalled."""
    from membrane import ch as CH
    from membrane import writes as W
    from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity
    for make in (lambda s, **k: NV.NativeVM(s, **k),):
        p = Path(tempfile.mkdtemp()) / "w.jsonl"
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        jn = J.Journal(p, est)
        jn.attach()
        clock = {"t": 1000.0}
        vm = NV.NativeVM(est, clock=lambda: clock["t"])
        jn.watch(vm)
        vm.attach()
        with W.as_writer("person:t"):
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + "toy:c", cls="toy", properties={"name": "c", "n": 0}), embedder=None)
            vm.define("tick", when=[["every", 10]], where=[["isa", "?x", "toy"]], write=["?x.n", "x.n + 1"])
        vm.drain()
        for _ in range(3):
            clock["t"] += 11
            vm.sweep()
            vm.drain()
        vm.detach()
        jn.close()
        fresh = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        rep = J.replay_vm(p, fresh, make)
        assert rep["mismatches"] == [] and rep["vm_writes"] == 3, rep


def _level_store():
    from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    for name, props, pos in (("die", {"name": "die", "a": 0}, [1.0, 0.0]),
                             ("anchor", {"name": "anchor"}, [0.0, 1.0]),
                             ("y", {"name": "y", "v": 0}, [0.0, 1.0])):
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + f"toy:{name}", cls="toy", properties=dict(props),
                                 position=np.array(pos, dtype=float)), embedder=None)
    return est


def _move_anchor(est):
    from membrane.entities import MEMBRANE_NS
    est.docs[MEMBRANE_NS + "toy:anchor"].position = np.array([1.0, 0.0], dtype=float)


def _edit_y(est):
    from membrane.entities import MEMBRANE_NS
    est.docs[MEMBRANE_NS + "toy:y"].properties["v"] = 9


_Y = ["is", "?y", "toy:y"]
_LEVEL_CASES = [
    ("near", [["changed", "?x.a"], ["near", "?x", "toy:anchor", 0.1]], [], _move_anchor),
    ("above", [["changed", "?x.a"], ["above", "y.v", 5]], [_Y], _edit_y),
    ("filter", [["changed", "?x.a"]], [_Y, ["filter", "y.v > 5"]], _edit_y),
]


@pytest.mark.parametrize("when,where,change", [c[1:] for c in _LEVEL_CASES], ids=[c[0] for c in _LEVEL_CASES])
def test_a_changed_trigger_with_a_condition_reads_what_was_changed_in_place(when, where, change):
    """Memo's re-review, A: a sweep skips `changed` triggers, but such a trigger's condition still reads
    the store when its write arrives; the native VM reads the mirror, so the sweep must refresh it."""
    from membrane import ch as CH
    from membrane import writes as W
    from membrane.entities import MEMBRANE_NS
    fired = {}
    for label, make in (("native", NV.NativeVM),):
        est = _level_store()
        vm = make(est)
        vm.attach()
        vm.define("t", when=when, where=[["isa", "?x", "toy"], *where], write=["?x.hit", "1"])
        change(est)
        vm.sweep()
        with W.as_writer("person:t"):
            est.edit(MEMBRANE_NS + "toy:die", properties={"a": 1})
        vm.drain()
        vm.detach()
        fired[label] = est.docs[MEMBRANE_NS + "toy:die"].properties.get("hit")
    assert fired["native"] == 1, fired


def test_a_bare_changed_trigger_still_costs_a_sweep_nothing():
    est = _level_store()
    vm = NV.NativeVM(est)
    vm.define("bare", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.hit", "1"])
    assert json.loads(vm.core.sweep_eids())["eids"] == []


# -- Memo's review of #972, finding B: a value the core's JSON can't carry never loses the write ------
def _both():
    from membrane import ch as CH
    return (("native", NV.NativeVM),)


@pytest.mark.parametrize("bad", [{"\udc80k": 1}, {"ok": {"\ud800": [1]}}, 10 ** 400, -(10 ** 400),
                                 [10 ** 400]], ids=lambda v: type(v).__name__)
def test_a_value_serde_cannot_read_still_fires_its_trigger(bad):
    from membrane.entities import MEMBRANE_NS
    die = MEMBRANE_NS + "toy:die"
    for label, make in _both():
        est = _level_store()
        vm = make(est)
        vm.attach()
        vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.hit", "1"])
        with _as_writer():
            est.edit(die, properties={"a": bad})
        vm.drain()
        assert est.docs[die].properties.get("hit") == 1, (label, vm.errors)
        est.docs[die].properties.pop("hit")
        with _as_writer():                     # the bad value is now the write's `old`
            est.edit(die, properties={"a": 2})
        vm.drain()
        vm.detach()
        assert est.docs[die].properties.get("hit") == 1, (label, vm.errors)
        assert not [k for k in vm.errors if k.startswith("hear:")], (label, vm.errors)


def test_clean_keeps_a_huge_integer_a_number_with_its_sign():
    big = NV._clean(10 ** 400)
    assert isinstance(big, float) and big == sys.float_info.max
    assert NV._clean(-(10 ** 400)) == -sys.float_info.max
    assert NV._clean(10 ** 300) == 10 ** 300            # serde can carry this one
    json.loads(NV._dumps(NV._clean({10 ** 5000: 1})))


# -- a `changed` trigger's write expression reads the live store, as ch.py does ------------------------
@pytest.mark.parametrize("expr,want", [("x.b", 7), ("x.a + x.b", 8), ("x.position", [9.0, 0.0])])
def test_a_bare_changed_trigger_reads_a_value_changed_in_place(expr, want):
    from membrane.entities import MEMBRANE_NS
    die = MEMBRANE_NS + "toy:die"
    got = {}
    for label, make in _both():
        est = _level_store()
        vm = make(est)
        vm.attach()
        vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.hit", expr])
        est.docs[die].properties["b"] = 7      # no write, so no event carries it
        est.docs[die].position = np.array([9.0, 0.0])
        with _as_writer():
            est.edit(die, properties={"a": 1})
        vm.drain()
        vm.detach()
        got[label] = est.docs[die].properties.get("hit")
    assert got == {"native": want}, got


def test_a_bare_changed_trigger_reads_another_entity_changed_in_place():
    from membrane.entities import MEMBRANE_NS
    die, y = MEMBRANE_NS + "toy:die", MEMBRANE_NS + "toy:y"
    got = {}
    for label, make in _both():
        est = _level_store()
        vm = make(est)
        vm.attach()
        vm.define("t", when=[["changed", "?x.a"]], where=[["is", "?x", "toy:die"], ["is", "?y", "toy:y"]],
                  write=["?x.hit", "y.v"])
        est.docs[y].properties["v"] = 7        # no write; only the store has it
        with _as_writer():
            est.edit(die, properties={"a": 1})
        vm.drain()
        vm.detach()
        got[label] = est.docs[die].properties.get("hit")
    assert got == {"native": 7}, got


def test_a_firing_that_names_no_entity_asks_the_store_nothing(monkeypatch):
    from membrane.entities import MEMBRANE_NS
    die = MEMBRANE_NS + "toy:die"
    est = _level_store()
    vm = NV.NativeVM(est)
    vm.attach()
    calls = []
    real = NV._Host.live
    monkeypatch.setattr(NV._Host, "live", lambda self, *a: (calls.append(a), real(self, *a))[1])
    vm.define("t", when=[["changed", "?x.a"]], where=[["isa", "?x", "toy"]], write=["?x.hit", "1"])
    with _as_writer():
        est.edit(die, properties={"a": 1})
    vm.drain()
    vm.detach()
    assert est.docs[die].properties.get("hit") == 1 and calls == []


def _as_writer():
    from membrane import writes as W
    return W.as_writer("person:t")
