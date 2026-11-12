"""The door starts from the boot (operator 2026-11-12: everything is c1f, launched by a c1f boot script): the launcher tells the
seat's facts -- `door_port`, `door_secret_file`, `ui_dir` -- and a setter in packs/door.c1f writes `Http:door.listen`; a real
request to that port is answered by the pack, through the engine the seat runs, with no Python route in the path."""
import os
import sys
import time
import types
import urllib.request
import urllib.error
from pathlib import Path

import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
pytest.importorskip("membrane.membrane_vm", reason="build it: native/build.sh")

from membrane import adapters as AD  # noqa: E402
from membrane import fields_live as FL  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

UI = str(Path(__file__).resolve().parent.parent / "membrane" / "ui")


def _until(fn, timeout=10.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def _seat(monkeypatch, tmp_path, facts):
    monkeypatch.setenv("MEMBRANE_FIELDS", "1")
    est = EntityStore(path=str(tmp_path / "e.json"), shared=False)
    srv = types.SimpleNamespace(entity_store=est, events_pool=WA.WatchEvents(), membrane=types.SimpleNamespace(),
                                _loop_runner=types.SimpleNamespace(sessions={}, hub=None))
    est._srv = srv
    FL.start(srv)
    AD.start(srv)
    FL.boot_started(est, facts=facts)
    return srv, est


def _get(port, path):
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10)
        return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def test_the_boot_facts_start_the_door_and_the_pack_answers_a_real_request(monkeypatch, tmp_path):
    secret = tmp_path / "jwt.secret"
    secret.write_text("0123456789abcdef0123456789abcdef")
    srv, est = _seat(monkeypatch, tmp_path, {"door_port": 0, "door_secret_file": str(secret), "ui_dir": UI})
    try:
        port = _until(lambda: ((est.docs.get("Http:door") and est.docs["Http:door"].properties.get("listen_result")) or {}).get("port"))
        assert port, "the door did not start"
        status, body, headers = _get(port, "/membrane")
        assert status == 200 and body == (Path(UI) / "index.html").read_bytes()
        assert "text/html" in headers.get("content-type", "")
        assert _get(port, "/ui/www/../index.html")[0] == 404
        assert _get(port, "/nowhere")[0] == 404
    finally:
        AD.stop()
        FL.stop(srv)


def test_without_a_door_port_there_is_no_door(monkeypatch, tmp_path):
    srv, est = _seat(monkeypatch, tmp_path, {"ui_dir": UI})
    try:
        time.sleep(1.0)
        assert "Http:door" not in est.docs or not est.docs["Http:door"].properties.get("listen_result")
    finally:
        AD.stop()
        FL.stop(srv)
