"""The About window (the operator, 2026-11-10): version, git short sha and the changelog, as a cliffhanger app --
membrane/packs/about.c1f draws it in the page's field engine; the seat feeds one About:seat entity (data only)."""
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import types
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import about as AB  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def test_the_seat_knows_its_version_sha_and_changelog():
    a = AB.read()
    from membrane.update import installed_version
    assert a["version"] == installed_version()
    if (ROOT / ".git").exists():
        sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        assert a["sha"] == sha
    assert a["changelog"].lstrip().startswith("## v")


def test_the_changelog_ships_in_the_package():
    assert (ROOT / "membrane" / "CHANGELOG.md").is_file() and not (ROOT / "membrane" / "CHANGELOG.md").is_symlink()
    for pp in (ROOT / "pyproject.toml", ROOT / "shim" / "membrane-seat" / "pyproject.toml"):
        assert "CHANGELOG.md" in tomllib.loads(pp.read_text())["tool"]["setuptools"]["package-data"]["membrane"], pp


def test_install_writes_the_about_entity():
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    AB.install(est)
    d = est.docs[AB.EID]
    assert d.cls == "About" and d.properties["version"] == AB.read()["version"]
    assert d.properties["changelog"] == AB.read()["changelog"]


def test_pages_get_the_pack_and_the_entity(monkeypatch):
    pytest.importorskip("membrane.membrane_vm")
    from membrane import fields_live as FL
    from membrane import watch_agents as WA
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=None,
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    live = FL.start(srv, thread=False)
    try:
        AB.install(est)
        live.settle()
        snap = live.snapshot()
        assert "about" in snap["packs"]
        assert any(e["eid"] == AB.EID for e in snap["entities"]), "About:seat is not sent to pages"
    finally:
        FL.stop(srv)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_view_in_the_page_engine():
    out = subprocess.run(["node", str(ROOT / "tests" / "js" / "about_view.test.mjs")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr[-2000:]


def test_the_top_bar_has_an_id_for_view_packs_and_styles_the_window():
    src = (ROOT / "membrane" / "ui" / "home.js").read_text()
    assert 'class="top glass" id="topbar"' in src
    assert ".top .aboutwrap" in src and ".top .aboutwrap > .about" in src     # under its button, in the panel's own CSS
