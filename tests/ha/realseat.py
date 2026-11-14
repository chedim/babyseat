"""A REAL cliffvm seat for the component's tests (#1143): the shipped packs, the door's secret (so every route needs a token, as on a user's
seat), a token minted by `cliffvm token mint`, and a FAKE `claude` script as the turn's binary. Never a real model, never a real `claude`;
loopback only; the child is always reaped. The pattern of native/tests/session_1143.rs.

The binary is native/target/{debug,release}/cliffvm (`cd native && cargo build --features bin`) or $CLIFFVM_BIN; no binary: the tests
that need a real seat are skipped with that command as the reason.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKS = ["boot.c1f", "fs.c1f", "proc.c1f", "fetch.c1f", "sql.c1f", "store.c1f", "door.c1f", "door_tools.c1f", "semtext.c1f", "mentions.c1f",
         "chat.c1f", "chat_turn.c1f", "door_session.c1f", "door_auth.c1f", "router.c1f", "door_models.c1f", "door_state.c1f", "ha.c1f"]

# a fake claude: answers "Done: <the prompt>" (the prompt is its last argument); a prompt with "this will fail" fails with the CLI's words
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, sys
def out(o): sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
prompt = sys.argv[-1]
out({"type": "system", "subtype": "init", "session_id": "q-1"})
if "this will fail" in prompt:
    sys.stderr.write("fake: not logged in\n"); sys.exit(3)
out({"type": "result", "subtype": "success", "is_error": False, "result": "Done: " + prompt, "num_turns": 1, "duration_ms": 9,
     "total_cost_usd": 0.0, "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1},
     "session_id": "q-1"})
'''


def binary() -> Path | None:
    cands = [os.environ.get("CLIFFVM_BIN", "")] + [str(ROOT / "native" / "target" / t / "cliffvm") for t in ("debug", "release")]
    return next((Path(c) for c in cands if c and Path(c).is_file()), None)


def need_binary() -> Path:
    b = binary()
    if b is None:
        pytest.skip("no cliffvm binary: cd native && cargo build --features bin")
    return b


class RealSeat:
    def __init__(self):
        self.bin = need_binary()
        self.dir = Path(tempfile.mkdtemp(prefix="ha-seat-"))
        self.child = None

    def start(self) -> "RealSeat":
        d = self.dir
        claude = d / "fake-claude"
        claude.write_text(FAKE_CLAUDE)
        claude.chmod(0o755)
        packs = d / "packs"
        packs.mkdir()
        for n in PACKS:
            shutil.copy(ROOT / "membrane" / "packs" / n, packs / n)
        shutil.copy(ROOT / "membrane" / "apps" / "barest.c1f" / "default_banner.c1f", packs / "default_banner.c1f")
        env = dict(os.environ, ANTHROPIC_API_KEY="sk-ant-harness-default-0000")
        self.child = subprocess.Popen(
            [str(self.bin), "run", str(packs / "boot.c1f"), str(packs), "--store", str(d / "field.sqlite3"), "--port", "0", "--ui-dir", str(d),
             "--fact", f"chat_claude_bin={claude}", "--fact", "seat_cwd=/srv/seat", "--fact", "seat_project=membrane", "--fact", "seat_version=1.0",
             "--fact", f"door_secret_file={d / 'door.secret'}", "--fact", f"seat_data_dir={d}"],
            cwd=d, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        line = self.child.stdout.readline()          # "...:<port>"
        self.port = int(line.rsplit(":", 1)[1].strip())
        self.url = f"http://127.0.0.1:{self.port}"
        t = time.time()
        while not (d / "door.secret").exists():
            assert time.time() - t < 30, "the seat made no door secret"
            time.sleep(0.05)
        return self

    def mint(self, sub="ha", ttl="1h", name="Home Assistant") -> str:
        r = subprocess.run([str(self.bin), "token", "mint", "--sub", sub, "--name", name, "--ttl", ttl, "--secret-file", str(self.dir / "door.secret")],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        return r.stdout.strip()

    def mint_at(self, exp_offset: float, sub="ha") -> str:
        """A token signed with the seat's own secret whose `exp` is now + `exp_offset` seconds (negative: long past; the door's leeway is 30 s,
        native/src/jwt.rs): `token mint` takes only a ttl of 1 s or more."""
        import base64, hashlib, hmac, json
        b = lambda o: base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).rstrip(b"=")
        now = int(time.time())
        head = b({"alg": "HS256", "typ": "JWT"}) + b"." + b({"sub": sub, "name": sub, "iat": now - 1000, "exp": now + int(exp_offset)})
        key = (self.dir / "door.secret").read_text().strip().encode()
        return (head + b"." + base64.urlsafe_b64encode(hmac.new(key, head, hashlib.sha256).digest()).rstrip(b"=")).decode()

    def stop(self) -> None:
        if self.child is not None:
            self.child.kill()
            self.child.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


@pytest.fixture
def real_seat():
    seat = RealSeat().start()
    try:
        yield seat
    finally:
        seat.stop()
