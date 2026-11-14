"""install.sh, the route for a user without pip (#1143, docs/packaging-plan.md): detect the platform, fetch the release tarball and its sha256, VERIFY before extracting and again against the
folder's MANIFEST.sha256, install under <prefix>/<version> with a babyseat link in the bin dir. No sudo, no network in these tests: the 'release' is a folder read through file://."""
import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from test_wheel_build import make_dist  # noqa: E402

INSTALL = ROOT / "install.sh"
VER = "9.8.7"
TARGET = "aarch64-apple-darwin"


def release(tmp_path, target=TARGET, tamper_inner=False, sidecar=True, bad_sidecar=False):
    """a release folder: babyseat-<ver>-<target>.tar.gz (+ .sha256), the way release-binaries.yml names them"""
    rel = tmp_path / "release"
    rel.mkdir(exist_ok=True)
    import shutil
    shutil.rmtree(tmp_path / "stage", ignore_errors=True)
    d = make_dist(tmp_path / "stage" / VER, VER)
    exe = d / "bin" / "cliffvm"
    exe.write_text('#!/bin/sh\necho "cliffvm $*"\n')
    exe.chmod(0o755)
    # re-stamp the manifest after the script edit
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(d).as_posix()}" for f in sorted(p for p in d.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")]
    (d / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    if tamper_inner:
        (d / "packs" / "boot.c1f").write_text("tampered after the manifest\n")
    tb = rel / f"babyseat-{VER}-{target}.tar.gz"
    with tarfile.open(tb, "w:gz") as t:
        t.add(d, arcname=VER)
    if sidecar:
        h = "0" * 64 if bad_sidecar else hashlib.sha256(tb.read_bytes()).hexdigest()
        (rel / (tb.name + ".sha256")).write_text(f"{h}  {tb.name}\n")
    return rel


def run(tmp_path, rel, *args, env=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    e = {"PATH": os.environ["PATH"], "HOME": str(home)}
    e.update(env or {})
    return subprocess.run(["sh", str(INSTALL), "--version", VER, "--base-url", f"file://{rel}", "--target", TARGET, *args], capture_output=True, text=True, env=e), home


def test_installs_under_the_version_folder_and_links_babyseat(tmp_path):
    r, home = run(tmp_path, release(tmp_path))
    assert r.returncode == 0, r.stderr + r.stdout
    root = home / ".local" / "share" / "membrane"
    assert (root / VER / "bin" / "cliffvm").is_file() and os.access(root / VER / "bin" / "cliffvm", os.X_OK)
    assert (root / VER / "VERSION").read_text().strip() == VER
    link = home / ".local" / "bin" / "babyseat"
    assert link.is_symlink()
    # the command starts the seat: `cliffvm serve <args>` (the fake binary echoes them)
    o = subprocess.run([str(link), "--port", "0"], capture_output=True, text=True)
    assert o.stdout.strip() == "cliffvm serve --port 0"
    # it says how to start, and the macOS note
    assert "babyseat" in r.stdout and "4096" in r.stdout
    assert "xattr" in r.stdout or "quarantine" in r.stdout
    # nothing is left half-installed
    assert not [p for p in root.iterdir() if ".partial" in p.name]


def test_version_flag_of_the_installed_command_is_the_binarys(tmp_path):
    r, home = run(tmp_path, release(tmp_path))
    assert r.returncode == 0
    o = subprocess.run([str(home / ".local" / "bin" / "babyseat"), "--version"], capture_output=True, text=True)
    assert o.stdout.strip() == "cliffvm --version"


def test_a_tarball_that_does_not_match_its_sha256_is_refused_before_anything_is_extracted(tmp_path):
    r, home = run(tmp_path, release(tmp_path, bad_sidecar=True))
    assert r.returncode != 0 and "sha256" in r.stderr
    assert not (home / ".local" / "share" / "membrane").exists() or not any((home / ".local" / "share" / "membrane").iterdir())
    assert not (home / ".local" / "bin" / "babyseat").exists()


def test_a_missing_sha256_is_refused_not_skipped(tmp_path):
    r, home = run(tmp_path, release(tmp_path, sidecar=False))
    assert r.returncode != 0 and "sha256" in r.stderr
    assert not (home / ".local" / "bin" / "babyseat").exists()


def test_a_folder_that_fails_its_own_manifest_is_refused(tmp_path):
    r, home = run(tmp_path, release(tmp_path, tamper_inner=True))
    assert r.returncode != 0 and "MANIFEST" in r.stderr
    root = home / ".local" / "share" / "membrane"
    assert not (root / VER).exists() and not (home / ".local" / "bin" / "babyseat").exists()


def test_a_failed_reinstall_keeps_the_working_install(tmp_path):
    r, home = run(tmp_path, release(tmp_path))
    assert r.returncode == 0
    r2, _ = run(tmp_path, release(tmp_path, bad_sidecar=True))
    assert r2.returncode != 0
    assert (home / ".local" / "share" / "membrane" / VER / "bin" / "cliffvm").is_file()
    assert (home / ".local" / "bin" / "babyseat").is_symlink()


def test_running_it_again_upgrades_in_place(tmp_path):
    rel = release(tmp_path)
    assert run(tmp_path, rel)[0].returncode == 0
    r, home = run(tmp_path, rel)
    assert r.returncode == 0, r.stderr
    assert subprocess.run([str(home / ".local" / "bin" / "babyseat"), "x"], capture_output=True, text=True).stdout.strip() == "cliffvm serve x"


def test_the_prefix_and_bin_dir_can_be_named(tmp_path):
    r, _ = run(tmp_path, release(tmp_path), "--prefix", str(tmp_path / "opt"), "--bin-dir", str(tmp_path / "bin"))
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "opt" / VER / "bin" / "cliffvm").is_file() and (tmp_path / "bin" / "babyseat").is_symlink()


@pytest.mark.parametrize("s,m,libc,want", [
    ("Darwin", "arm64", "", "aarch64-apple-darwin"),
    ("Darwin", "x86_64", "", "x86_64-apple-darwin"),
    ("Linux", "x86_64", "gnu", "x86_64-unknown-linux-gnu"),
    ("Linux", "aarch64", "gnu", "aarch64-unknown-linux-gnu"),
    ("Linux", "arm64", "gnu", "aarch64-unknown-linux-gnu"),
    ("Linux", "x86_64", "musl", "x86_64-unknown-linux-musl"),
    ("Linux", "aarch64", "musl", "aarch64-unknown-linux-musl"),
])
def test_the_platform_is_detected(s, m, libc, want):
    env = {"PATH": os.environ["PATH"], "MEMBRANE_UNAME_S": s, "MEMBRANE_UNAME_M": m, "MEMBRANE_LIBC": libc}
    o = subprocess.run(["sh", str(INSTALL), "--print-target"], capture_output=True, text=True, env=env)
    assert o.returncode == 0 and o.stdout.strip() == want, o.stderr


@pytest.mark.parametrize("s,m", [("Windows_NT", "x86_64"), ("Linux", "armv7l"), ("FreeBSD", "amd64")])
def test_an_unsupported_platform_says_so_and_how_else_to_install(s, m):
    env = {"PATH": os.environ["PATH"], "MEMBRANE_UNAME_S": s, "MEMBRANE_UNAME_M": m}
    o = subprocess.run(["sh", str(INSTALL), "--print-target"], capture_output=True, text=True, env=env)
    assert o.returncode != 0 and "no binary for this platform" in o.stderr and "pip" in o.stderr


def test_no_sudo_and_no_unchecked_pipes_in_the_script():
    code = [l for l in INSTALL.read_text().splitlines() if not l.lstrip().startswith("#")]
    assert not [l for l in code if "sudo" in l or "su -c" in l]
    assert "set -eu" in INSTALL.read_text()
    assert INSTALL.read_text().startswith("#!/bin/sh")


def test_build_sh_archive_has_the_sha256_sidecar_install_sh_checks(tmp_path):
    fake = tmp_path / "cliffvm"
    fake.write_text('#!/bin/sh\necho "cliffvm $*"\n')
    fake.chmod(0o755)
    ver = (ROOT / "native" / "Cargo.toml").read_text().split('version = "')[1].split('"')[0]
    tb = tmp_path / f"babyseat-{ver}-{TARGET}.tar.gz"
    subprocess.run(["sh", str(ROOT / "scripts" / "dist" / "build.sh"), "--bin", str(fake), "--out", str(tmp_path / "o" / ver), "--archive", str(tb)], check=True, capture_output=True)
    side = (tmp_path / (tb.name + ".sha256")).read_text()
    assert side == f"{hashlib.sha256(tb.read_bytes()).hexdigest()}  {tb.name}\n"
    # and the installer takes that very release
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(["sh", str(INSTALL), "--version", ver, "--base-url", f"file://{tmp_path}", "--target", TARGET], capture_output=True, text=True, env={"PATH": os.environ["PATH"], "HOME": str(home)})
    assert r.returncode == 0, r.stderr
    assert (home / ".local" / "share" / "membrane" / ver / "packs" / "boot.c1f").is_file()
