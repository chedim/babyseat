"""The c1f stdlib's fs and proc packs on a live field (#1044): a person's write runs a command or writes a file
(native, off the engine); the receipt lands in the store; an entity script's write is refused; fs reads by getters."""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
MV = pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane import writes as W  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


def _wait(cond, timeout=10.0):
    t = time.time() + timeout
    while time.time() < t:
        if cond():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def seat(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    monkeypatch.chdir(tmp_path)                                   # the seat's folder (no SQLite store here)
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None)
    est._srv = srv
    live = FL.start(srv)
    yield srv
    FL.stop(srv)


def _p(srv, eid):
    d = srv.entity_store.docs.get(eid)
    return (d.properties or {}) if d is not None else {}


def _set(srv, eid, cls, writer="person:owner", **props):
    est = srv.entity_store
    d = est.docs.get(eid)
    with W.as_writer(writer):
        if d is None:
            est.upsert(QuantumEntity(eid=eid, cls=cls, properties=props), embedder=None)
        else:
            d.properties = {**(d.properties or {}), **props}
            est.upsert(d, embedder=None)


def test_a_command_runs_and_its_receipt_lands_in_the_store(seat):
    assert {"File", "Dir", "Command"} <= seat.fields_live.classes
    _set(seat, "Command:hello", "Command", cmd=["sh", "-c", "echo hello from $0", "c1f"])
    _set(seat, "Command:hello", "Command", run={"id": "r1"})
    assert _wait(lambda: (_p(seat, "Command:hello").get("run_result") or {}).get("id") == "r1"), _p(seat, "Command:hello")
    p = _p(seat, "Command:hello")
    assert p["run_result"]["stdout"] == "hello from c1f\n" and p["run_rc"] == 0
    assert _wait(lambda: _p(seat, "Command:hello").get("output") == "hello from c1f\n")
    assert _p(seat, "Command:hello").get("ok") is True


def test_an_entity_script_can_not_run_a_command(seat):
    _set(seat, "Command:hello", "Command", cmd="echo should not run > ran.txt")
    _set(seat, "Command:hello", "Command", writer="script:Component:x", run={"id": "s1"})
    assert _wait(lambda: (_p(seat, "Command:hello").get("run_result") or {}).get("id") == "s1")
    assert _p(seat, "Command:hello")["run_result"]["rc"] == 2
    assert not os.path.exists("ran.txt")


def test_fs_reads_and_writes_through_the_pack(seat, tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    _set(seat, "File:hello", "File", path="hello.txt")
    assert _wait(lambda: _p(seat, "File:hello").get("text") == "hi"), _p(seat, "File:hello")
    _set(seat, "File:out", "File", path="out/new.txt")
    _set(seat, "File:out", "File", write={"id": "w1", "content": "written by c1f"})
    assert _wait(lambda: (_p(seat, "File:out").get("write_result") or {}).get("rc") == 0)
    assert (tmp_path / "out" / "new.txt").read_text() == "written by c1f"
