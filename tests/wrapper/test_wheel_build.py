"""The platform wheel (#1143, docs/packaging-plan.md): a prepared payload (scripts/dist/assemble_wheel_payload.sh) becomes `py3-none-<platform>` with the dist folder inside as
`membrane/dist/`; no payload means no wheel (the sdist FAILS LOUDLY). These tests build with a fake `cliffvm` (a shell script), so no cargo runs; they need `build` + setuptools
in the python that runs them (skipped otherwise: CI installs them)."""
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from membrane import __version__  # noqa: E402

try:
    import build  # noqa: F401
    import setuptools  # noqa: F401
    HAVE_BUILD = True
except ImportError:
    HAVE_BUILD = False
needs_build = pytest.mark.skipif(not HAVE_BUILD, reason="needs the `build` and `setuptools` packages")
BUILD_WHEEL = ROOT / "scripts" / "dist" / "build_wheel.sh"


def make_dist(d: Path, version: str = __version__) -> Path:
    """a dist folder in the shape scripts/dist/build.sh makes, with a fake binary"""
    (d / "bin").mkdir(parents=True)
    (d / "packs").mkdir()
    (d / "apps" / "barest.c1f").mkdir(parents=True)
    (d / "help").mkdir()
    exe = d / "bin" / "cliffvm"
    exe.write_text('#!/bin/sh\necho "cliffvm ' + version + '"\n')
    exe.chmod(0o755)
    (d / "packs" / "boot.c1f").write_text("PACK boot 1\n")
    (d / "apps" / "barest.packs").write_text("boot\n")
    (d / "apps" / "barest.c1f" / "app.c1f").write_text("x\n")
    (d / "help" / "start.md").write_text("# start\n")
    (d / "LICENSE").write_text("MIT\n")
    (d / "VERSION").write_text(version + "\n")
    lines = []
    for f in sorted(p for p in d.rglob("*") if p.is_file()):
        lines.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(d).as_posix()}")
    (d / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    return d


def build_wheel(tmp_path, tag="macosx_11_0_arm64", version=__version__):
    dist = make_dist(tmp_path / "src-dist", version)
    out = tmp_path / "out"
    r = subprocess.run(["sh", str(BUILD_WHEEL), tag, "--dist", str(dist), "--out", str(out)],
                       capture_output=True, text=True, env={**os.environ, "PYTHON": sys.executable})
    return r, out


@needs_build
def test_the_wheel_is_tagged_for_the_platform_and_carries_the_dist_folder(tmp_path):
    r, out = build_wheel(tmp_path)
    assert r.returncode == 0, r.stderr + r.stdout
    wheels = list(out.glob("*.whl"))
    assert [w.name for w in wheels] == [f"babyseat-{__version__}-py3-none-macosx_11_0_arm64.whl"]
    z = zipfile.ZipFile(wheels[0])
    names = set(z.namelist())
    for f in ("bin/cliffvm", "packs/boot.c1f", "apps/barest.packs", "apps/barest.c1f/app.c1f", "help/start.md", "VERSION", "MANIFEST.sha256", "LICENSE"):
        assert f"membrane/dist/{f}" in names, f
    for f in ("membrane/__init__.py", "membrane/launcher.py", "membrane/__main__.py"):
        assert f in names
    # the executable bit survives the zip, and the stamped version is the wheel's
    mode = z.getinfo("membrane/dist/bin/cliffvm").external_attr >> 16
    assert mode & stat.S_IXUSR and mode & stat.S_IXOTH
    assert z.read("membrane/dist/VERSION").decode().strip() == __version__
    # nothing of the repo's other folders rides along
    assert not [n for n in names if n.startswith(("native/", "tests/", "docs/", "custom_components/"))]


@needs_build
def test_both_console_scripts_are_in_the_wheel(tmp_path):
    r, out = build_wheel(tmp_path)
    assert r.returncode == 0, r.stderr
    z = zipfile.ZipFile(next(out.glob("*.whl")))
    ep = z.read(f"babyseat-{__version__}.dist-info/entry_points.txt").decode()
    assert "babyseat = membrane.launcher:main" in ep and "membrane = membrane.launcher:main" in ep
    assert "Root-Is-Purelib: false" in z.read(f"babyseat-{__version__}.dist-info/WHEEL").decode()


@needs_build
@pytest.mark.parametrize("tag", ["manylinux_2_28_x86_64", "musllinux_1_2_aarch64", "macosx_10_9_x86_64"])
def test_every_platform_tag_the_ci_names_makes_its_wheel(tmp_path, tag):
    r, out = build_wheel(tmp_path, tag)
    assert r.returncode == 0, r.stderr
    assert [w.name for w in out.glob("*.whl")] == [f"babyseat-{__version__}-py3-none-{tag}.whl"]


@needs_build
def test_a_payload_of_another_version_is_refused(tmp_path):
    r, out = build_wheel(tmp_path, version="0.0.1")
    assert r.returncode != 0 and "0.0.1" in r.stderr + r.stdout and __version__ in r.stderr + r.stdout
    assert not list(out.glob("*.whl")) if out.exists() else True


@needs_build
def test_a_payload_that_fails_its_manifest_is_refused(tmp_path):
    dist = make_dist(tmp_path / "d")
    (dist / "packs" / "boot.c1f").write_text("tampered\n")
    r = subprocess.run(["sh", str(BUILD_WHEEL), "macosx_11_0_arm64", "--dist", str(dist), "--out", str(tmp_path / "o")],
                       capture_output=True, text=True, env={**os.environ, "PYTHON": sys.executable})
    assert r.returncode != 0 and "MANIFEST" in r.stderr + r.stdout


def _copy_sources(dst: Path) -> Path:
    # the repo's own wrapper files AND its whole membrane/ folder (packs, apps, help: the data the dist folder ships): the sdist must still be the launcher alone
    dst.mkdir()
    for f in ("pyproject.toml", "setup.py", "README.md", "LICENSE"):
        shutil.copy(ROOT / f, dst / f)
    shutil.copytree(ROOT / "membrane", dst / "membrane", ignore=shutil.ignore_patterns("__pycache__", "*.so"))
    return dst


@needs_build
def test_the_sdist_has_no_binary_and_building_a_wheel_from_it_fails_loudly(tmp_path):
    src = _copy_sources(tmp_path / "src")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BABYSEAT_")}
    r = subprocess.run([sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(tmp_path / "sd"), str(src)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    sd = next((tmp_path / "sd").glob("*.tar.gz"))
    with tarfile.open(sd) as t:
        names = t.getnames()
        t.extractall(tmp_path / "x", filter="data")
    assert not [n for n in names if "cliffvm" in n or "/dist/" in n]
    assert sorted(n.split("/", 1)[1] for n in names if n.count("/") >= 2 and not n.endswith("/") and "egg-info" not in n) == ["membrane/__init__.py", "membrane/__main__.py", "membrane/launcher.py"], names
    assert any(n.endswith("/membrane/launcher.py") for n in names)
    r = subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(tmp_path / "w"), str(next((tmp_path / "x").iterdir()))],
                       capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "no binary for this platform" in r.stdout + r.stderr
    assert not list((tmp_path / "w").glob("*.whl")) if (tmp_path / "w").exists() else True
