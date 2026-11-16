"""THE UPGRADE PATH (#1143): `pip install -U babyseat` brings the wrapper wheel, a user with a Python-seat data dir (MEMBRANE_DATA_DIR, `field.sqlite/field.sqlite3` inside) starts `babyseat`,
the launcher execs `cliffvm serve`, which passes MEMBRANE_DATA_DIR as the fact `legacy_path`, and the cutover pack (packs/legacy_cutover.c1f) finds that store and imports from it.
Runs the real binary: needs CLIFFVM_BIN (a built cliffvm; its dist folder is made with scripts/dist/build.sh), skipped otherwise. The seat is private: a free port, its own data dir, a fake claude, no chat turn."""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = os.environ.get("CLIFFVM_BIN")
pytestmark = pytest.mark.skipif(not BIN or not Path(BIN).is_file(), reason="needs CLIFFVM_BIN (a built cliffvm)")


def read(port, token, spec, prop):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "entity_read", "arguments": {"spec": spec, "prop": prop}}}).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", body, {"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    t = json.loads(urllib.request.urlopen(r, timeout=15).read())["result"]["content"][0]["text"]
    return None if " has no " in t or not t else t


@pytest.fixture
def dist(tmp_path):
    out = tmp_path / "dist"
    subprocess.run(["sh", str(ROOT / "scripts" / "dist" / "build.sh"), "--bin", BIN, "--out", str(out)], check=True, capture_output=True)
    return out


def start_launcher(dist, tmp_path, old):
    """the wheel's launcher (`python -m membrane`, the console scripts' entry), the binary named by $CLIFFVM, MEMBRANE_DATA_DIR = the old seat's data dir"""
    data = tmp_path / "data"
    env = {**os.environ, "CLIFFVM": str(dist / "bin" / "cliffvm"), "CLAUDE_BIN": "/bin/sh", "MEMBRANE_DATA_DIR": str(old)}
    env.pop("ANTHROPIC_API_KEY", None)
    log = tmp_path / "serve.log"
    p = subprocess.Popen([sys.executable, "-m", "membrane", "--port", "0", "--data-dir", str(data)], cwd=ROOT, env={**env, "PYTHONPATH": str(ROOT)},
                         stdout=open(log, "w"), stderr=subprocess.STDOUT)
    port = None
    for _ in range(300):
        m = re.search(r"door listening on .*:(\d+)", log.read_text())
        if m and (data / "admin.token").exists():
            port = int(m.group(1)); break
        assert p.poll() is None, log.read_text()
        time.sleep(0.1)
    assert port, log.read_text()
    return p, port, (data / "admin.token").read_text().strip()


def old_store(tmp_path):
    old = tmp_path / "old-python-seat-data"
    (old / "field.sqlite").mkdir(parents=True)
    cx = sqlite3.connect(old / "field.sqlite" / "field.sqlite3")
    cx.execute("CREATE TABLE c_sessions (sid TEXT)")
    cx.commit(); cx.close()
    return old


def test_the_launcher_passes_the_old_data_dir_and_the_cutover_picks_the_store_up(dist, tmp_path):
    old = old_store(tmp_path)
    p, port, tok = start_launcher(dist, tmp_path, old)
    try:
        assert read(port, tok, "Boot:seat", "legacy_path") == str(old), "MEMBRANE_DATA_DIR reaches the seat as the fact legacy_path"
        # the cutover found THE store of that folder (its path is the ledger's) and is importing or has finished: never absent, never an error about a missing store
        state = path = None
        for _ in range(300):
            state, path = read(port, tok, "Legacy:cutover", "state"), read(port, tok, "Legacy:cutover", "path")
            if state in ("done", "importing", "error") and path:
                break
            time.sleep(0.1)
        assert path == str(old / "field.sqlite" / "field.sqlite3"), (state, path)
        assert state in ("done", "importing"), (state, read(port, tok, "Legacy:cutover", "reason"))
    finally:
        p.terminate(); p.wait(timeout=30)


def test_a_named_old_dir_with_no_store_is_an_error_with_its_path_not_a_silent_fresh_start(dist, tmp_path):
    empty = tmp_path / "no-store-here"
    empty.mkdir()
    p, port, tok = start_launcher(dist, tmp_path, empty)
    try:
        state = None
        for _ in range(300):
            state = read(port, tok, "Legacy:cutover", "state")
            if state:
                break
            time.sleep(0.1)
        assert state == "error"
        assert str(empty) in (read(port, tok, "Legacy:cutover", "reason") or "")
    finally:
        p.terminate(); p.wait(timeout=30)
