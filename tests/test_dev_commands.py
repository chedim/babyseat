"""The dev commands (operator, 2026-11-12): on a no-bash seat a worker had to define a git Command before it could use
git, and the definitions split into a seven-level task tree. The `dev` pack (membrane/packs/dev.c1f) makes
Command:git and Command:gh by WRITING to nodes: a setter on Boot.started writes their `cmd` (a write to
`<Class>:<key>` of a class the pack imports creates the entity). The seat writes Boot:seat.started once, after the
field engine is up -- data only, as About:seat is."""
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
    yield est, live
    FL.stop(srv)


def test_the_seat_starts_with_git_and_gh_as_commands(seat):
    est, live = seat
    assert "Command:git" not in est.docs
    FL.boot_started(est)
    live.settle()
    for name in ("git", "gh"):
        d = est.docs[f"Command:{name}"]
        assert d.cls == "Command" and d.properties["cmd"] == [name]


def test_a_second_boot_changes_nothing_but_the_stamp(seat):
    est, live = seat
    FL.boot_started(est); live.settle()
    FL.boot_started(est); live.settle()
    assert est.docs["Command:git"].properties["cmd"] == ["git"]
    assert est.docs["Boot:seat"].cls == "Boot" and est.docs["Boot:seat"].properties["started"]


def test_the_pack_loads_without_errors(seat):
    est, live = seat
    assert "dev" in live.snapshot()["packs"]
    import json
    assert "dev" not in json.loads(live.f.pack_errors() or "{}")      # (the about pack waits for dom in a seat: not ours)
