"""The dist folder carries the graphlink-bridge program next to cliffvm when the build made one (the seat's peers run it: docs/graph-link.md, section 18)."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def build(bin_dir: Path, out: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["sh", str(ROOT / "scripts" / "dist" / "build.sh"), "--bin", str(bin_dir / "cliffvm"), "--out", str(out)], capture_output=True, text=True)


def fake(d: Path, *names: str) -> Path:
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_text("#!/bin/sh\necho " + n + "\n")
        (d / n).chmod(0o755)
    return d


def test_the_bridge_beside_the_binary_goes_into_bin_and_the_manifest():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = build(fake(t / "target", "cliffvm", "graphlink-bridge"), t / "dist")
        assert r.returncode == 0, r.stderr
        b = t / "dist" / "bin" / "graphlink-bridge"
        assert b.is_file() and os.access(b, os.X_OK)
        assert "bin/graphlink-bridge" in (t / "dist" / "MANIFEST.sha256").read_text()


def test_a_build_with_no_bridge_beside_the_binary_still_builds():
    with tempfile.TemporaryDirectory() as t:
        t = Path(t)
        r = build(fake(t / "target", "cliffvm"), t / "dist")
        assert r.returncode == 0, r.stderr
        assert not (t / "dist" / "bin" / "graphlink-bridge").exists()
