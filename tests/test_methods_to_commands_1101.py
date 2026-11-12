"""#1101: entity methods become Command entities at boot (membrane/packs/methods.c1f; the pack's behavior is pinned in
tests/specs/methods.json). Here: the seat's own entity store -- a method entity as night.method_define makes it (a Tool
with method_name, command, method_of, name) reaches the engine, and a boot writes its Command, the rest on Methods:review."""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import fields_live as FL  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    live = FL.start(srv, thread=False)
    yield srv, est, live
    FL.stop(srv)


def test_a_defined_method_is_a_command_after_boot_and_a_step_method_is_listed(seat):
    srv, est, live = seat
    est.upsert(QuantumEntity(eid="host", cls="Component", properties={"name": "host"}), embedder=None)
    assert N.method_define(srv, est, None, None, "host", "greet", "echo hello {{who}}")["ok"]
    assert N.method_define(srv, est, None, None, "host", "two", "", steps=[{"name": "a", "command": "echo 1"}])["ok"]
    FL.boot_started(est)
    live.settle()
    cmd = est.docs["Command:host_greet"]
    assert cmd.cls == "Command" and cmd.properties["cmd"] == 'echo hello "$1"'
    assert cmd.properties["params"] == ["who"] and cmd.properties["read_only"] is False
    rev = est.docs["Methods:review"].properties["untranslated"]
    assert [r["name"] for r in rev] == ["host.two"] and rev[0]["why"] == "steps or a guard (parents)"
    assert "methods" not in json.loads(live.f.pack_errors() or "{}")
