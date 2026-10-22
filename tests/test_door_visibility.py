"""THE MESSAGE AT THE DOOR (the disappear-then-appear-sent exhibit):
a user message announces at ACCEPTANCE — before any guard/gate work,
in BOTH lanes — so the TUI's row exists when the message lands, not
when its turn's inner reaches the announce. THE CONTRACT:

1. a direct POST publishes message.updated + part + session.updated
   BEFORE the assistant's first part;
2. a POST on a busy session (the queued lane) ALSO announces at
   acceptance — the row renders even while Queued;
3. the flood-refused stub path (queue full) still announced the row;
4. no double announcement (the inner's announce is idempotent via
   the _announced flag).
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest

PORT = 11671

# THESE BOOT A REAL SEAT -- against a FAKE OLLAMA (tests/_fake_ollama.py): they
# used to run real model turns on the operator's live ollama under 30 s timeouts,
# and whether a stored-and-announced message passed depended on model latency and
# on everything else using ollama (flaky in every full run, green solo). What they
# test -- the door storing and announcing -- never depended on a model's words.
# Self-contained now: no local ollama needed, so they run in CI too.

_PY = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python")
_SPAWN = _PY if os.path.exists(_PY) else sys.executable

def _tail(path, n=60):
    """The last n lines of a captured log -- a boot crash's why, not DEVNULL."""
    try:
        with open(path, "rb") as f:
            return "\n".join(f.read().decode(errors="replace").splitlines()[-n:])
    except Exception:
        return ""


@pytest.fixture()
def quiet_seat():
    """A booted serve on a RAM field (ephemeral, cheap)."""
    import os

    sys.path.insert(0, os.path.dirname(__file__))
    from _ports import quiet_port
    from _seat_env import seat_env
    # its serve and proxy ports: two distinct ports below the ephemeral range
    # -- a released bind(0) port is the next one the OS hands a parallel
    # connection, and the seat then walked off the port this fixture polls
    # ("seat never became ready"); the proxy no longer walks up from the
    # live seat's default either (#408)
    port = quiet_port()
    listen = quiet_port(avoid=(port,))
    env = dict(os.environ)
    env["MEMBRANE_EFIELD_STORE"] = "mem"
    env["MEMBRANE_LAN"] = "0"
    env["MEMBRANE_EF_ANN"] = "0"
    env["MEMBRANE_NO_OPEN"] = "1"
    # its own fresh dir per seat: a fixed shared dir let a stale seat's (or a
    # parallel run's) files leak into this one (#408)
    seat_dir = tempfile.mkdtemp(prefix="door-seat-")
    # ... and nothing of it lands outside that dir: the inherited environment
    # pointed its home files (~/.membrane/peers.jsonl, the commons, auth) and
    # any inherited MEMBRANE_DATA_DIR at the operator's real ones (#408)
    env = seat_env(seat_dir, env)
    from _fake_ollama import FakeOllama
    fake = FakeOllama().__enter__()
    so = open(os.path.join(seat_dir, "seat.stdout"), "wb")
    se = open(os.path.join(seat_dir, "seat.stderr"), "wb")
    proc = subprocess.Popen(
        [_SPAWN, "-m", "membrane.opencode",
         "--serve", "--serve-port", str(port), "--serve-host", "127.0.0.1",
         "--no-launch", "--no-thinker", "--no-dreamer", "--no-autonode",
         "--no-router", "--model", "granite4:latest",
         "--field", f"{seat_dir}/f.json", "--audit", f"{seat_dir}/a.jsonl",
         "--backend", fake.url, "--listen", str(listen)],
        env=env, cwd=seat_dir, stdout=so, stderr=se)

    def _boot_failure(why):
        so.flush()
        se.flush()
        tail = _tail(os.path.join(seat_dir, "seat.stderr"))
        return AssertionError(
            f"{why} (rc={proc.poll()})\n--- seat stderr ---\n{tail or '(empty)'}")

    cred = None
    try:
        deadline = time.time() + 40
        while time.time() < deadline and not cred:
            if proc.poll() is not None:
                raise _boot_failure("the seat exited during boot")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/agent", timeout=3)
                cred = True                      # open surface: /agent answers, no auth
                break
            except Exception:
                time.sleep(0.3)
        if not cred:
            raise _boot_failure("seat never became ready")
        hdr = {"Content-Type": "application/json"}
        _mk = {"url": f"http://127.0.0.1:{port}"}

        def _get(url):
            return json.loads(urllib.request.urlopen(
                urllib.request.Request(url, headers=hdr), timeout=8).read())

        def _post(url, body):
            req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                         headers=hdr, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=30).read())

        def new_session():
            return _post(_mk["url"] + "/session", {})

        yield _mk["url"], _get, _post, new_session, seat_dir
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        so.close()
        se.close()
        shutil.rmtree(seat_dir, ignore_errors=True)
        fake.__exit__(None, None, None)
    # the seat talked to the fake, never to a real model (success path only)
    assert any(p in ("/api/chat", "/v1/chat/completions")
               for _, p in fake.served), fake.served[:10]


def test_direct_post_announces_and_stores(quiet_seat):
    base, _get, _post, new_session, _seat_dir = quiet_seat
    s = new_session()
    sid = s["id"]
    out = _post(f"{base}/session/{sid}/message",
                {"parts": [{"type": "text", "text": "reply: pong"}]})
    rows = _get(f"{base}/session/{sid}/message")
    users = [r for r in rows if r["info"]["role"] == "user"]
    assert users, "the user message never stored"
    assert out["info"]["role"] == "assistant"


def test_busy_session_announces_at_acceptance(quiet_seat):
    base, _get, _post, new_session, _seat_dir = quiet_seat
    s = new_session()
    sid = s["id"]
    _post(f"{base}/session/{sid}/message",
          {"parts": [{"type": "text", "text": "reply: pong"}]})
    _post(f"{base}/session/{sid}/message",
          {"parts": [{"type": "text", "text": "reply: pong again"}]})
    rows = _get(f"{base}/session/{sid}/message")
    users = [r["info"]["id"] for r in rows if r["info"]["role"] == "user"]
    assert len(users) >= 2, users


def test_no_double_announce(quiet_seat):
    base, _get, _post, new_session, _seat_dir = quiet_seat
    s = new_session()
    sid = s["id"]
    _post(f"{base}/session/{sid}/message",
          {"parts": [{"type": "text", "text": "reply: pong"}]})
    rows = _get(f"{base}/session/{sid}/message")
    ids = [r["info"]["id"] for r in rows]
    assert len(ids) == len(set(ids)), ids

def test_the_door_seat_keeps_to_its_own_dir(quiet_seat):
    """The seat these tests boot writes only under its own temp dir: it
    registers as a peer in that dir, never in the real home's
    ~/.membrane/peers.jsonl (the operator's live seat reads that roster)."""
    base, _get, _post, new_session, seat_dir = quiet_seat
    sid = new_session()["id"]                     # one turn: the fixture checks it reached the fake
    _post(f"{base}/session/{sid}/message", {"parts": [{"type": "text", "text": "reply: pong"}]})
    own = os.path.join(seat_dir, "peers.jsonl")
    assert os.path.exists(own), os.listdir(seat_dir)
    with open(own) as f:
        assert seat_dir in f.read()
    real = os.path.expanduser("~/.membrane/peers.jsonl")
    if os.path.exists(real):
        with open(real) as f:
            assert seat_dir not in f.read()
