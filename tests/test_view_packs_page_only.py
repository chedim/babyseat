"""A pack that imports `dom` is a VIEW pack: only a page's engine has `dom`. The seat sends it to pages and keeps its
classes mirrored; it waits in the engine (a Pack entity says why) but the seat LOG does not report it as an error
(live 2026-11-12: Memo read `pack about: imports dom 1, which isn't loaded` as a failure). A pack waiting for a pack
that loads a moment later (file order) is not an error either."""
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
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402


@pytest.fixture
def seat(monkeypatch):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    yield est, srv
    FL.stop(srv)


def test_the_seat_reports_no_error_for_a_view_pack(seat, capsys):
    est, srv = seat
    live = FL.start(srv, thread=False)
    err = capsys.readouterr().err
    assert "imports dom" not in err and "isn't loaded" not in err, err
    assert "about" in live.snapshot()["packs"]               # pages still get it
    assert "About" in live._view_classes()                   # and its entity is mirrored for them


def test_the_view_pack_still_says_why_it_waits_on_its_pack_entity(seat):
    est, srv = seat
    live = FL.start(srv, thread=False)
    assert "imports dom 1, which isn't loaded" in json.loads(live.f.pack_errors() or "{}")["about"]


def test_a_shipped_pack_that_waits_for_a_missing_import_is_reported_once(seat, capsys, monkeypatch, tmp_path):
    est, srv = seat
    (tmp_path / "orphan.c1f").write_text("PACK orphan 1\nIMPORT nothere 1\nThing.x getter: 1\n")
    monkeypatch.setattr(FL, "PACK_DIR", tmp_path)
    live = FL.start(srv, thread=False)
    live._scan_files()
    err = capsys.readouterr().err
    assert err.count("imports nothere 1, which isn't loaded") == 1, err
