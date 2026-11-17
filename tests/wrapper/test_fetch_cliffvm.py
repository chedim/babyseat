"""scripts/dist/fetch_cliffvm.sh: the engine's release binary for one platform, from the cliffvm repository's release, checked before it is used (a fake release in a folder, over file://)."""
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FETCH = ROOT / "scripts" / "dist" / "fetch_cliffvm.sh"
TARGET = "aarch64-apple-darwin"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def release(base: Path, version: str = "0.1.1", target: str = TARGET, *, bad_sum=False, bad_manifest=False, stamp=None) -> None:
    name = f"cliffvm-{version}-{target}"
    files = {"bin/cliffvm": b"#!/bin/sh\necho cliffvm " + version.encode() + b"\n", "bin/graphlink-bridge": b"#!/bin/sh\necho bridge\n", "LICENSE": b"MIT\n", "VERSION": (stamp or version).encode() + b"\n"}
    manifest = "".join(f"{sha(files[f] if not (bad_manifest and f == 'LICENSE') else b'other')}  {f}\n" for f in sorted(files))
    files["MANIFEST.sha256"] = manifest.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for f, data in files.items():
            ti = tarfile.TarInfo(f"{name}/{f}")
            ti.size = len(data)
            ti.mode = 0o755 if f.startswith("bin/") else 0o644
            t.addfile(ti, io.BytesIO(data))
    blob = buf.getvalue()
    d = base / f"v{version}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.tar.gz").write_bytes(blob)
    (d / f"{name}.tar.gz.sha256").write_text(f"{'0' * 64 if bad_sum else sha(blob)}  {name}.tar.gz\n")


def run(base: Path, out: Path, *args: str) -> subprocess.CompletedProcess:
    args = args or ("0.1.1", TARGET, str(out))
    return subprocess.run(["sh", str(FETCH), *args], capture_output=True, text=True, env={**os.environ, "CLIFFVM_RELEASE_BASE": f"file://{base}"})


def test_the_release_for_a_platform_is_fetched_checked_and_unpacked_to_the_folder_named():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        release(t / "rel")
        r = run(t / "rel", t / "engine")
        assert r.returncode == 0, r.stderr
        assert (t / "engine" / "bin" / "cliffvm").is_file() and os.access(t / "engine" / "bin" / "cliffvm", os.X_OK)
        assert (t / "engine" / "bin" / "graphlink-bridge").is_file()
        assert (t / "engine" / "VERSION").read_text().strip() == "0.1.1"


def test_a_tarball_whose_checksum_is_wrong_is_refused_and_nothing_is_left():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        release(t / "rel", bad_sum=True)
        r = run(t / "rel", t / "engine")
        assert r.returncode != 0 and "sha256" in r.stderr, r.stderr
        assert not (t / "engine").exists()


def test_a_tarball_whose_manifest_does_not_match_its_files_is_refused():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        release(t / "rel", bad_manifest=True)
        r = run(t / "rel", t / "engine")
        assert r.returncode != 0 and "MANIFEST" in r.stderr, r.stderr
        assert not (t / "engine").exists()


def test_a_tarball_that_says_another_version_than_the_one_asked_for_is_refused():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        release(t / "rel", stamp="0.9.9")
        r = run(t / "rel", t / "engine")
        assert r.returncode != 0 and "0.9.9" in r.stderr, r.stderr


def test_a_release_that_is_not_there_is_an_error_naming_what_was_asked_for():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        (t / "rel").mkdir()
        r = run(t / "rel", t / "engine")
        assert r.returncode != 0 and "cliffvm-0.1.1-" + TARGET in r.stderr, r.stderr


def test_the_version_the_target_and_the_folder_are_all_required():
    with tempfile.TemporaryDirectory() as t:
        r = run(Path(t), Path(t) / "x", "0.1.1")
        assert r.returncode == 2 and "usage" in r.stderr
