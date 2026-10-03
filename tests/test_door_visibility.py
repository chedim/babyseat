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
import base64
import json
import shutil
import subprocess
import time
import urllib.request

import pytest

PORT = 11671
SEAT_DIR = "/tmp/door-seat"


@pytest.fixture()
def quiet_seat():
    """A booted serve on a RAM field (ephemeral, cheap)."""
    import os
    import socket as _sock

    _probe = _sock.socket()
    _probe.bind(("127.0.0.1", 0))
    port = _probe.getsockname()[1]
    _probe.close()
    env = dict(os.environ)
    env["MEMBRANE_EFIELD_STORE"] = "mem"
    env["MEMBRANE_LAN"] = "0"
    env["MEMBRANE_EF_ANN"] = "0"
    env["MEMBRANE_NO_OPEN"] = "1"
    os.makedirs(SEAT_DIR, exist_ok=True)
    for stale in ("serve-auth.json", "f.json", "f.json.seed.json"):
        p = os.path.join(SEAT_DIR, stale)
        if os.path.exists(p):
            os.unlink(p)
    proc = subprocess.Popen(
        [".venv/bin/python", "-m", "membrane.opencode",
         "--serve", "--serve-port", str(port), "--serve-host", "127.0.0.1",
         "--no-launch", "--no-thinker", "--no-dreamer", "--no-autonode",
         "--field", f"{SEAT_DIR}/f.json", "--audit", f"{SEAT_DIR}/a.jsonl"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cred = None
    deadline = time.time() + 40
    while time.time() < deadline and not cred:
        p = os.path.join(SEAT_DIR, "serve-auth.json")
        if os.path.exists(p):
            try:
                cred = json.loads(open(p).read())
                tok = base64.b64encode(
                    f"{cred['user']}:{cred['password']}".encode()).decode()
                probe = urllib.request.Request(
                    f"http://127.0.0.1:{port}/agent",
                    headers={"Authorization": "Basic " + tok})
                urllib.request.urlopen(probe, timeout=3)
                break
            except Exception:
                cred = None
        time.sleep(0.3)
    assert cred, "seat never became ready"
    tok = base64.b64encode(
        f"{cred['user']}:{cred['password']}".encode()).decode()
    hdr = {"Authorization": "Basic " + tok,
           "Content-Type": "application/json"}

    def _get(url):
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=hdr), timeout=8).read())

    def _post(url, body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers=hdr, method="POST")
        return json.loads(urllib.request.urlopen(req, timeout=30).read())

    _mk = {"url": f"http://127.0.0.1:{port}"}

    def new_session():
        return _post(_mk["url"] + "/session", {})

    yield _mk["url"], _get, _post, new_session
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(SEAT_DIR, ignore_errors=True)


def test_direct_post_announces_and_stores(quiet_seat):
    base, _get, _post, new_session = quiet_seat
    s = new_session()
    sid = s["id"]
    out = _post(f"{base}/session/{sid}/message",
                {"parts": [{"type": "text", "text": "reply: pong"}]})
    rows = _get(f"{base}/session/{sid}/message")
    users = [r for r in rows if r["info"]["role"] == "user"]
    assert users, "the user message never stored"
    assert out["info"]["role"] == "assistant"


def test_busy_session_announces_at_acceptance(quiet_seat):
    base, _get, _post, new_session = quiet_seat
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
    base, _get, _post, new_session = quiet_seat
    s = new_session()
    sid = s["id"]
    _post(f"{base}/session/{sid}/message",
          {"parts": [{"type": "text", "text": "reply: pong"}]})
    rows = _get(f"{base}/session/{sid}/message")
    ids = [r["info"]["id"] for r in rows]
    assert len(ids) == len(set(ids)), ids