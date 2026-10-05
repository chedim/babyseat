"""THE /V1 POST HANG (the live seat's wedge: GET /agent + GET
/v1/models answer in ms, but EVERY POST /v1/chat/completions hangs
the socket — three probes, three 20-45s timeouts). THE CONTRACT: a
POST to the seat's /v1 answers within 30s on a local model — the
same seat that serves GETs fast. The test boots the seat, probes
GETs (fast), then POSTs turns (must complete); three sequential
POSTs, none may hang past 30s warm.
"""
import base64
import json
import shutil
import socket
import subprocess
import time
import urllib.request

import pytest

SEAT_DIR = "/tmp/v1hang-seat"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture()
def boot():
    import os
    port = _free_port()
    env = dict(os.environ)
    env.update({"MEMBRANE_EFIELD_STORE": "mem", "MEMBRANE_LAN": "0",
                "MEMBRANE_NO_OPEN": "1"})
    os.makedirs(SEAT_DIR, exist_ok=True)
    for stale in ("serve-auth.json", "opencode.pid"):
        p = os.path.join(SEAT_DIR, stale)
        if os.path.exists(p):
            os.unlink(p)
    # THE STDERR IS THE BOOT'S OWN MAP: '/v1' = the PROXY's port
    # (its own); '/agent' = the SERVE's (another -- the two-surface
    # seat). The boot logs both; the test reads the PROXY's REAL
    # port from the log (a stdout line: 'membrane opencode  http://
    # 127.0.0.1:NNNN/v1').
    logf = open(f"{SEAT_DIR}/boot.log", "w")
    proc = subprocess.Popen(
        [".venv/bin/python", "-m", "membrane.opencode",
         "--serve", "--serve-port", str(port), "--no-launch",
         "--no-thinker", "--no-dreamer", "--no-autonode",
         "--no-router", "--model", "granite4:latest",
         "--field", f"{SEAT_DIR}/f.json", "--audit", f"{SEAT_DIR}/a.jsonl"],
        env=env, stdout=logf, stderr=subprocess.STDOUT)
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
    assert cred, "seat never booted"
    tok = base64.b64encode(
        f"{cred['user']}:{cred['password']}".encode()).decode()
    hdr = {"Authorization": "Basic " + tok,
           "Content-Type": "application/json"}
    # THE PROXY'S REAL PORT (the /v1 lane's own): from the boot log:
    proxy_port = None
    deadline2 = time.time() + 30
    while time.time() < deadline2 and not proxy_port:
        logf.flush()
        txt = open(f"{SEAT_DIR}/boot.log").read()
        import re as _re
        m = _re.search(r"http://127\.0\.0\.1:(\d+)/v1", txt)
        if m:
            proxy_port = int(m.group(1))
            break
        time.sleep(0.3)
    assert proxy_port, "the boot never logged its /v1 lane"
    base = f"http://127.0.0.1:{proxy_port}"

    def get(url, t=10):
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=hdr), timeout=t).read())

    def post(url, body, t=30):
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(url, data=json.dumps(body).encode(),
                                   headers=hdr, method="POST"),
            timeout=t).read())

    yield {"base": f"http://127.0.0.1:{proxy_port}",
           "serve_base": f"http://127.0.0.1:{port}",
           "get": get, "post": post, "port": port}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    shutil.rmtree(SEAT_DIR, ignore_errors=True)


def test_gets_answer_then_post_answers(boot):
    """THE WEDGE'S CONTRACT: GET /v1/models fast; a POST turn answers
    (the local granite rung, cold-load allowed for); NO hang."""
    base = boot["base"]
    t0 = time.time()
    rows = boot["get"](base + "/v1/models")
    assert (time.time() - t0) < 5.0
    assert any("granite4" in m["id"] for m in rows["data"])
    # THE POST: the local turn (granite's cold load = the one-time
    # cost; the budget covers it):
    t1 = time.time()
    out = boot["post"](base + "/v1/chat/completions",
                       {"model": "granite4:latest",
                        "messages": [{"role": "user",
                                      "content": "reply: pong"}]},
                       t=120)
    el = time.time() - t1
    assert out["choices"][0]["message"]["content"], out
    assert el < 120.0


def test_three_sequential_posts_no_hang(boot):
    """THREE POSTs, sequential; each within 120s; the warm ones
    within 30s (the cold load's cost = the first's)."""
    base = boot["base"]
    times = []
    for i in range(3):
        t1 = time.time()
        out = boot["post"](base + "/v1/chat/completions",
                           {"model": "granite4:latest",
                            "messages": [
                                {"role": "user",
                                 "content": f"reply with {i+1}"}]},
                           t=120)
        times.append(time.time() - t1)
        assert out["choices"][0]["message"]["content"]
    assert times[0] < 120.0
    assert all(t < 30.0 for t in times[1:]), times