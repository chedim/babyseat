"""scripts/dist/build.sh around a binary that is not built here: the product's version is pyproject.toml's (VERSION), the engine's is stamped beside it (ENGINE), and the smoke test checks the engine's own version against ENGINE."""
import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def fake_bin(d: Path, says: str) -> Path:
    d.mkdir(parents=True)
    exe = d / "cliffvm"
    exe.write_text(f'#!/bin/sh\n[ "$1" = "--version" ] && echo "cliffvm {says}" && exit 0\nexit 1\n')
    exe.chmod(0o755)
    return exe


def build(exe: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(ROOT / "scripts" / "dist" / "build.sh"), "--bin", str(exe), "--out", str(out), *extra], capture_output=True, text=True)


def test_the_dist_says_its_own_version_from_pyproject_and_the_engines_from_the_option():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = build(fake_bin(t / "b", "0.1.1"), t / "dist", "--engine-version", "0.1.1")
        assert r.returncode == 0, r.stderr
        assert (t / "dist" / "VERSION").read_text().strip() == PRODUCT
        assert (t / "dist" / "ENGINE").read_text().strip() == "0.1.1"
        assert "ENGINE" in (t / "dist" / "MANIFEST.sha256").read_text()


def test_without_the_option_there_is_no_engine_stamp():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = build(fake_bin(t / "b", PRODUCT), t / "dist")
        assert r.returncode == 0, r.stderr
        assert not (t / "dist" / "ENGINE").exists()


def smoke(dist: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(ROOT / "scripts" / "dist" / "smoke.sh"), str(dist)], capture_output=True, text=True)


def test_the_smoke_test_expects_the_binary_to_say_the_engine_version_when_there_is_one():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        build(fake_bin(t / "good", "0.1.1"), t / "dist-good", "--engine-version", "0.1.1")
        # the binary says the engine version: the version check passes (the smoke then stops at `serve`, which the fake binary cannot do)
        assert "does not say" not in smoke(t / "dist-good").stderr
        build(fake_bin(t / "bad", "0.2.0"), t / "dist-bad", "--engine-version", "0.1.1")
        r = smoke(t / "dist-bad")
        assert r.returncode != 0 and "does not say 0.1.1" in r.stderr, r.stderr


def test_without_an_engine_stamp_the_smoke_test_still_expects_the_product_version():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        build(fake_bin(t / "bad", "0.0.1"), t / "dist")
        r = smoke(t / "dist")
        assert r.returncode != 0 and f"does not say {PRODUCT}" in r.stderr, r.stderr
