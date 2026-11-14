"""The launcher is the only Python the seat has (#1143): it finds the binary and execs it. Nothing here starts a seat."""
import os
import stat
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from membrane import __version__, launcher  # noqa: E402


@pytest.fixture
def exe(tmp_path, monkeypatch):
    """a fake binary, named by $CLIFFVM; execv is recorded instead of run"""
    p = tmp_path / "cliffvm"
    p.write_text("#!/bin/sh\n")
    p.chmod(p.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("CLIFFVM", str(p))
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    return p, calls


def test_the_arguments_are_forwarded_to_serve_untouched(exe):
    p, calls = exe
    launcher.main(["--port", "4311", "--fact", "operator=ann"])
    assert calls == [(str(p), [str(p), "serve", "--port", "4311", "--fact", "operator=ann"])]


def test_no_arguments_is_just_serve(exe):
    p, calls = exe
    launcher.main([])
    assert calls == [(str(p), [str(p), "serve"])]


def test_the_wheels_binary_is_found_without_the_override(tmp_path, monkeypatch):
    monkeypatch.delenv("CLIFFVM", raising=False)
    here = tmp_path / "membrane"
    b = here / "dist" / "bin"
    b.mkdir(parents=True)
    (b / "cliffvm").write_text("#!/bin/sh\n")
    (b / "cliffvm").chmod(0o755)
    monkeypatch.setattr(launcher, "HERE", here)
    assert launcher.binary() == str(b / "cliffvm")


def test_the_override_beats_the_wheels_binary(exe, monkeypatch, tmp_path):
    p, _ = exe
    here = tmp_path / "membrane"
    (here / "dist" / "bin").mkdir(parents=True)
    (here / "dist" / "bin" / "cliffvm").write_text("x")
    (here / "dist" / "bin" / "cliffvm").chmod(0o755)
    monkeypatch.setattr(launcher, "HERE", here)
    assert launcher.binary() == str(p)


def test_no_binary_says_so_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CLIFFVM", raising=False)
    monkeypatch.setattr(launcher, "HERE", tmp_path / "nowhere" / "membrane")
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("nothing to exec"))
    with pytest.raises(SystemExit) as e:
        launcher.main([])
    assert e.value.code == 1
    assert "no binary for this platform" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", launcher.GONE)
def test_every_dead_subcommand_exits_2_with_the_way_back(cmd, exe, capsys):
    _, calls = exe
    with pytest.raises(SystemExit) as e:
        launcher.main([cmd, "--anything"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert cmd in err and "babyseat==0.7.53" in err
    assert calls == []


def test_version_prints_the_wheels_version_and_needs_no_binary(monkeypatch, capsys):
    monkeypatch.delenv("CLIFFVM", raising=False)
    with pytest.raises(SystemExit) as e:
        launcher.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"babyseat {__version__}"


def test_the_version_is_pyprojects():
    assert __version__ == tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_python_m_membrane_is_the_launcher():
    assert (ROOT / "membrane" / "__main__.py").read_text().split() == ["from", ".launcher", "import", "main", "main()"]


def test_the_console_scripts_point_at_the_launcher():
    s = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert s == {"membrane": "membrane.launcher:main", "babyseat": "membrane.launcher:main"}


def test_the_launcher_holds_no_seat_logic():
    # no flag parsing, no store path, no secret: the file stays small
    src = (ROOT / "membrane" / "launcher.py").read_text()
    assert len(src.splitlines()) < 60
    for w in ("argparse", "sqlite", "jwt", "ANTHROPIC", "subprocess"):
        assert w not in src
