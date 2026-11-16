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


@pytest.mark.parametrize("argv", [["--backend", "http://ollama:11434"], ["--port", "4096", "--model", "m"], ["--serve-port=4096"],
                                  ["--cortex"], ["--cells", "3"], ["--loops", "2"], ["--field", "x"], ["--think"], ["--listen", "4097"]])
def test_a_flag_of_the_old_python_seat_names_the_flags_serve_takes_and_exits_2(argv, exe, capsys):
    _, calls = exe
    with pytest.raises(SystemExit) as e:
        launcher.main(argv)
    assert e.value.code == 2 and calls == []
    err = capsys.readouterr().err
    flag = next(a for a in argv if a.split("=")[0] in launcher.OLD_FLAGS).split("=")[0]
    assert flag in err
    for f in ("--port", "--data-dir", "--bind", "--fact name=value"):
        assert f in err, f
    assert len(err.strip().splitlines()) <= 5


def test_the_old_flags_are_a_short_list_of_exact_names_and_none_is_a_serve_flag():
    assert 5 <= len(launcher.OLD_FLAGS) <= 30
    assert not {"--port", "--data-dir", "--bind", "--fact", "--store", "--root", "--legacy"} & set(launcher.OLD_FLAGS)


def test_a_flag_that_only_starts_like_an_old_one_is_passed_through(exe):
    p, calls = exe
    launcher.main(["--fact", "model=--model", "--models-dir", "x"])          # values and unknown flags are cliffvm's to judge
    assert calls == [(str(p), [str(p), "serve", "--fact", "model=--model", "--models-dir", "x"])]


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
    assert len(src.splitlines()) < 70
    for w in ("argparse", "sqlite", "jwt", "ANTHROPIC", "subprocess"):
        assert w not in src


# ---- the packaged binary: its dist folder must be this wheel's (#1143, packaging) ----
def _dist(tmp_path, monkeypatch, version, mode=0o755):
    monkeypatch.delenv("CLIFFVM", raising=False)
    here = tmp_path / "membrane"
    (here / "dist" / "bin").mkdir(parents=True)
    exe = here / "dist" / "bin" / "cliffvm"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(mode)
    if version is not None:
        (here / "dist" / "VERSION").write_text(version + "\n")
    monkeypatch.setattr(launcher, "HERE", here)
    return exe


def test_a_dist_of_another_version_is_refused_and_nothing_is_exec(tmp_path, monkeypatch, capsys):
    _dist(tmp_path, monkeypatch, "0.0.1")
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("a mismatched binary must not run"))
    with pytest.raises(SystemExit) as e:
        launcher.main([])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "0.0.1" in err and __version__ in err and "pip install" in err


def test_a_dist_without_a_version_file_is_refused(tmp_path, monkeypatch):
    _dist(tmp_path, monkeypatch, None)
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("no VERSION: not this wheel's"))
    with pytest.raises(SystemExit) as e:
        launcher.main([])
    assert e.value.code == 1


def test_the_matching_dist_runs_serve_with_the_arguments(tmp_path, monkeypatch):
    exe = _dist(tmp_path, monkeypatch, __version__)
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append((path, argv)))
    launcher.main(["--port", "0"])
    assert calls == [(str(exe), [str(exe), "serve", "--port", "0"])]


def test_a_lost_exec_bit_is_restored_not_fatal(tmp_path, monkeypatch):
    exe = _dist(tmp_path, monkeypatch, __version__, mode=0o644)
    calls = []
    monkeypatch.setattr(os, "execv", lambda path, argv: calls.append(path))
    launcher.main([])
    assert calls == [str(exe)] and os.access(exe, os.X_OK)


def test_the_override_is_not_version_checked(exe):
    # a developer's own build (CLIFFVM) runs whatever it is
    _, calls = exe
    launcher.main([])
    assert len(calls) == 1
