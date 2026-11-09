"""#971 step 2 (red first): the write journal and replay -- the contract a native VM must pass.

The journal records every field write with its writer and chain; a birth carries the entity's class,
properties and position (a bare `(eid, None, eid)` event can't rebuild it). Replaying a journal's outside
writes (everything not written by the VM itself, `trigger:*`) into a fresh store with a fresh VM must
reproduce the recorded trigger writes exactly, and the store's final state."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import ch as CH  # noqa: E402
from membrane import journal as J  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


def _store():
    return EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)


def _write(est, eid, prop, value, who="person:ann"):
    with W.source("person", "ann"), W.as_writer(who):
        ent = est.docs[eid]
        ent.properties[prop] = value
        est.upsert(ent, embedder=None)


def _session(path):
    """A small recorded session: two entities, a trigger, a few person writes that fire it."""
    est = _store()
    jn = J.Journal(path, est)
    jn.attach()
    vm = CH.VM(est)
    vm.attach()
    try:
        with W.source("person", "ann"), W.as_writer("person:ann"):
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + "Room:kitchen", cls="Room",
                                     properties={"name": "kitchen", "temp": 20}, position=[0.1, 0.2, 0.3]),
                       embedder=None)
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + "Fan:f1", cls="Fan", properties={"name": "f1", "on": False}),
                       embedder=None)
            vm.define("fan follows heat", when=[["changed", "?r.temp"]],
                      where=[["isa", "?r", "Room"]],
                      write=[MEMBRANE_NS + "Fan:f1.on", "new > 25"])
        vm.drain()
        for t in (22, 27, 30, 24):
            _write(est, MEMBRANE_NS + "Room:kitchen", "temp", t)
            vm.drain()
    finally:
        vm.detach()
        jn.close()
    return est


def _state(est):
    return {e.eid: (e.cls, dict(e.properties or {}), [round(float(x), 6) for x in (e.position or [])])
            for e in est.docs.values()}


def test_the_journal_records_writes_with_their_writer_and_births_with_their_content():
    p = Path(tempfile.mkdtemp()) / "w.jsonl"
    _session(p)
    lines = list(J.read(p))
    born = [ln for ln in lines if ln.get("born")]
    kitchen = next(ln for ln in born if ln["eid"] == MEMBRANE_NS + "Room:kitchen")
    assert kitchen["born"]["cls"] == "Room" and kitchen["born"]["props"]["name"] == "kitchen"
    assert kitchen["born"]["position"] == [0.1, 0.2, 0.3]
    temps = [ln["new"] for ln in lines if ln["path"] == MEMBRANE_NS + "Room:kitchen.temp"]
    assert temps == [22, 27, 30, 24]
    assert all(ln["writer"] == "person:ann" for ln in lines if ln["path"].endswith(".temp"))
    fan = [(ln["writer"].split(":")[0], ln["new"]) for ln in lines if ln["path"] == MEMBRANE_NS + "Fan:f1.on"]
    assert ("trigger", True) in fan and ("trigger", False) in fan


def test_replaying_every_write_rebuilds_the_store():
    p = Path(tempfile.mkdtemp()) / "w.jsonl"
    est = _session(p)
    fresh = _store()
    J.replay(p, fresh)
    assert _state(fresh) == _state(est)


def test_replaying_the_outside_writes_through_a_fresh_vm_reproduces_its_writes():
    """The native VM's contract: same outside writes in, same trigger writes out, same final state."""
    p = Path(tempfile.mkdtemp()) / "w.jsonl"
    est = _session(p)
    fresh = _store()
    out = Path(tempfile.mkdtemp()) / "replayed.jsonl"
    rep = J.replay_vm(p, fresh, lambda s: CH.VM(s), record=out)
    assert rep["mismatches"] == [], rep["mismatches"][:5]
    assert rep["vm_writes"] > 0
    assert _state(fresh) == _state(est)
