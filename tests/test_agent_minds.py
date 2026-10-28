"""THE AGENT LIST IS THE LIVE MIND: /agent reports the loop pool's 3
workers as agent rows (each row shows its claim -- the entity a running
loop is processing, or idle) + the thinker and dreamer as rows carrying
their live stats. The operator's ask: the pool of thinking agents
APPEARS IN opencode's agent list."""
import json
import os
import socket
import subprocess
import sys
import urllib.request

import pytest

# THE INTERPRETER: the dev venv when the checkout has one, else the
# running pytest interpreter (CI installs the package with -e .).
_PY = os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "python")
_SPAWN = _PY if os.path.exists(_PY) else sys.executable

# A REAL SEAT BOOT (its router wants a backend): no ollama here (CI) --
# the seat never became ready there (the CI exhibit) -- skip the module.
_HOST = "127.0.0.1"
_PORT = int(os.environ.get("OLLAMA_PORT", "11434"))


def _ollama_alive():
    try:
        socket.create_connection((_HOST, _PORT), timeout=1).close()
        return True
    except OSError:
        return False


if not _ollama_alive():
    pytest.skip("needs a local ollama (the live lane)", allow_module_level=True)


def _get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


@pytest.fixture()
def seat(tmp_path):
    """A booted native serve on an ephemeral field (open surface: no auth)."""
    import time

    port = 11641
    sys.path.insert(0, os.path.dirname(__file__))
    from _seat_env import seat_env
    env = seat_env(tmp_path)                 # writes only under tmp_path (#408)
    env["MEMBRANE_EF_ANN"] = "0"
    proc = subprocess.Popen(
        [_SPAWN, "-m", "membrane.opencode",
         "--serve", "--serve-port", str(port), "--serve-host", "127.0.0.1",
         "--field", str(tmp_path / "rag-field.json")],
        env=env, cwd=str(tmp_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 40
        ready = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/agent", timeout=3) as r:
                    json.loads(r.read())
                ready = True
                break
            except Exception:
                time.sleep(0.3)
        assert ready, "seat never became ready"

        def _get(url: str):
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read())
        yield base, _get
    finally:                                 # a failed boot stops its seat too
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_agent_list_reports_the_loop_pool_and_minds(seat):
    base, _get = seat
    agents = _get(base + "/agent")
    names = [a["name"] for a in agents]
    # the 3 loop workers, by slot number:
    slots = [n for n in names if n.startswith("loop-")]
    assert slots == ["loop-1", "loop-2", "loop-3"], names
    # the ambient minds:
    assert "thinker" in names
    assert "dreamer" in names
    # each loop row describes its claim state (idle or the entity):
    for a in agents:
        if a["name"].startswith("loop-"):
            assert "idle" in a["description"] or "processing" in a["description"]
    # the thinker row speaks its stats:
    th = next(a for a in agents if a["name"] == "thinker")
    assert "beats" in th["description"]
    dr = next(a for a in agents if a["name"] == "dreamer")
    assert "branch" in dr["description"]


def test_a_seat_that_never_boots_is_still_stopped(tmp_path, monkeypatch):
    """The fixture stops its seat even when the boot wait fails (an orphan
    seat outlived its pytest run on 2026-10-27)."""
    import time
    stopped = []

    class _Proc:
        def terminate(self):
            stopped.append("terminate")

        def wait(self, timeout=None):
            return 0

        def kill(self):
            stopped.append("kill")

    clock = iter(range(0, 1000, 50))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(time, "time", lambda: next(clock))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    gen = seat.__wrapped__(tmp_path)
    with pytest.raises(AssertionError):
        next(gen)
    assert stopped == ["terminate"]
