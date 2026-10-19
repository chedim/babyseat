"""THE /V1 POST HANG (the live seat's wedge: GET /agent + GET
/v1/models answer in ms, but EVERY POST /v1/chat/completions hangs
the socket — three probes, three 20-45s timeouts). THE CONTRACT: a
POST to the seat's /v1 answers — the same seat that serves GETs fast.
The test boots the seat, probes GETs (fast), then POSTs turns (must
complete); three sequential POSTs, none may hang.

WHAT A HANG IS HERE (#227): a POST that never answers. The model behind
the seat is a SHARED local ollama -- other seats, other test runs and
other worktrees queue on it, so a warm turn's wall-clock is not the
seat's to promise. The hang line is the socket timeout (generous, and
still finite: the wedge never answered at all), never a tight stopwatch
on someone else's queue.

ISOLATION (#227): every boot gets its OWN seat dir (pytest's tmp_path)
-- the seat's single-instance lock (membrane.pid beside --field) made a
shared /tmp dir refuse the second of two concurrent runs ('seat never
booted'), and one run's teardown rmtree'd the other's log. Both ports
are read from the seat's OWN log (the seat may re-pick a busy port), and
a seat that exits during boot fails fast with its log.
"""
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

# THE LIVE SEAT (a local ollama + a real granite turn): a checkout without
# one (CI) skips at import instead of failing on the missing server.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PY = os.path.join(_REPO, ".venv", "bin", "python")
_SPAWN = _PY if os.path.exists(_PY) else sys.executable
_HOST = "127.0.0.1"
_PORT = int(os.environ.get("OLLAMA_PORT", "11434"))

BOOT_BUDGET = 180.0     # a cold interpreter + seat on a loaded box
POST_BUDGET = 300.0     # the hang line: a POST that never answers


def _ollama_alive() -> bool:
    try:
        socket.create_connection((_HOST, _PORT), timeout=1).close()
        return True
    except OSError:
        return False


if not _ollama_alive():
    pytest.skip("needs a local ollama (the live lane)", allow_module_level=True)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _tail(logp, n=40):
    try:
        with open(logp, errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError as e:
        return f"(no log: {e})"


def _wait_for(pred, proc, logp, budget, what):
    """Poll pred() until truthy; fail fast (with the log) if the seat exits."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        got = pred()
        if got:
            return got
        if proc.poll() is not None:
            pytest.fail(f"the seat exited ({proc.returncode}) before {what}:\n"
                        + _tail(logp))
        time.sleep(0.2)
    pytest.fail(f"{what} not within {budget:.0f}s:\n" + _tail(logp))


@pytest.fixture()
def boot(tmp_path):
    seat = tmp_path / "seat"
    seat.mkdir()
    env = dict(os.environ)
    env.update({"MEMBRANE_EFIELD_STORE": "mem", "MEMBRANE_LAN": "0",
                "MEMBRANE_NO_OPEN": "1",
                # the seat runs IN its own dir (absorb reads cwd); this
                # checkout's code stays the code under test:
                "PYTHONPATH": os.pathsep.join(
                    p for p in (_REPO, env.get("PYTHONPATH")) if p)})
    logp = seat / "boot.log"
    logf = open(logp, "w")
    proc = subprocess.Popen(
        [_SPAWN, "-m", "membrane.opencode",
         "--serve", "--serve-port", str(_free_port()),
         "--listen", str(_free_port()), "--no-launch",
         "--no-thinker", "--no-dreamer", "--no-autonode",
         "--no-router", "--model", "granite4:latest",
         "--field", str(seat / "f.json"), "--audit", str(seat / "a.jsonl")],
        cwd=str(seat), env=env, stdout=logf, stderr=subprocess.STDOUT)

    def _grep(pat):
        def find():
            m = re.search(pat, logp.read_text(errors="replace"))
            return int(m.group(1)) if m else None
        return find

    hdr = {"Content-Type": "application/json"}

    def get(url, t=10):
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers=hdr), timeout=t).read())

    def post(url, body, t=POST_BUDGET):
        return json.loads(urllib.request.urlopen(
            urllib.request.Request(url, data=json.dumps(body).encode(),
                                   headers=hdr, method="POST"),
            timeout=t).read())

    try:
        # THE BOOT'S OWN MAP: '/v1' = the PROXY's port; 'serve:' = the
        # native serve's (the two-surface seat) -- both from THIS seat's
        # log (a busy pick is re-picked by the seat, never assumed).
        serve_port = _wait_for(
            _grep(r"\[opencode\] serve: http://[^:\s]+:(\d+)"),
            proc, logp, BOOT_BUDGET, "the serve lane's port")
        proxy_port = _wait_for(
            _grep(r"http://127\.0\.0\.1:(\d+)/v1"),
            proc, logp, BOOT_BUDGET, "the /v1 lane's port")

        def _agent_up():
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{serve_port}/agent", timeout=5)
                return True
            except Exception:
                return False
        _wait_for(_agent_up, proc, logp, BOOT_BUDGET, "GET /agent")

        yield {"base": f"http://127.0.0.1:{proxy_port}",
               "serve_base": f"http://127.0.0.1:{serve_port}",
               "get": get, "post": post, "port": serve_port}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=15)
        logf.close()


def test_gets_answer_then_post_answers(boot):
    """THE WEDGE'S CONTRACT: GET /v1/models fast; a POST turn answers
    (the local granite rung, cold-load allowed for); NO hang."""
    base = boot["base"]
    t0 = time.monotonic()
    rows = boot["get"](base + "/v1/models")
    # the GET never waits on a generation: fast even on a loaded box
    assert (time.monotonic() - t0) < 10.0
    assert any("granite4" in m["id"] for m in rows["data"])
    # THE POST: the local turn; a hang = the socket timeout raising
    out = boot["post"](base + "/v1/chat/completions",
                       {"model": "granite4:latest",
                        "messages": [{"role": "user",
                                      "content": "reply: pong"}]})
    assert out["choices"][0]["message"]["content"], out


def test_three_sequential_posts_no_hang(boot):
    """THREE POSTs, sequential; each must answer (none may hang past
    POST_BUDGET -- the wedge hung every POST, the first included)."""
    base = boot["base"]
    for i in range(3):
        out = boot["post"](base + "/v1/chat/completions",
                           {"model": "granite4:latest",
                            "messages": [
                                {"role": "user",
                                 "content": f"reply with {i+1}"}]})
        assert out["choices"][0]["message"]["content"], out
