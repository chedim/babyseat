"""#440 part 4: THE SEAT SUPERVISES BLURP'S PROCESS (membrane.self_super).

Behind MEMBRANE_SELF_PROC (default ON since #440 part 5; =0 keeps the in-process loop
as before). With the flag on the seat does not start the in-process loop: a
supervisor spawns membrane.self_proc only while Blurp's mode is on or watch,
restarts it on a crash (2 s, doubling, capped at 60 s), holds it off after 5
crashes in 10 minutes (shown on the card and the panel's status), stops it
with SIGTERM then SIGKILL after 10 s, and forwards /membrane/self/* and the
self controls to its loopback port (5 s, then the last good answer).

Tests that start a real child stop it on every path and keep it in tmp_path."""
import json
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from membrane import self_loop as SL
from membrane import self_proc as SP
from membrane import self_super as SUP

from tests.test_self_proc_440 import T0, _felt, _lines  # noqa: F401
from tests.test_self_proc_440 import _data  # noqa: F401  autouse fixture
from tests.test_self_proc_440 import _call, _child_env, _open_env, _served


# ---- the approved constants ---------------------------------------------------------------

def test_the_supervisor_uses_the_approved_constants():
    assert SUP.BACKOFF_START_S == 2.0        # #440 constant 2
    assert SUP.BACKOFF_MAX_S == 60.0
    assert SUP.HOLD_CRASHES == 5             # #440 constant 3
    assert SUP.HOLD_WINDOW_S == 600.0
    assert SUP.STOP_GRACE_S == 10.0          # #440 constant 4
    assert SUP.FORWARD_TIMEOUT_S == 5.0      # #440 constant 5
    assert SUP.FLAG == SP.FLAG == "MEMBRANE_SELF_PROC"


# ---- the crash policy -----------------------------------------------------------------------

def test_a_crash_restarts_after_a_doubling_backoff_and_five_in_ten_minutes_hold_it():
    c = SUP.Crashes()
    assert [c.crash(t) for t in (0.0, 1.0, 2.0, 3.0)] == [2.0, 4.0, 8.0, 16.0]
    assert c.crash(4.0) is None and c.held                # the fifth in the window: held
    assert c.crash(5.0) is None                           # stays held
    c.clear()
    assert not c.held and c.crash(6.0) == 2.0             # the person turned it on again: afresh


def test_crashes_older_than_the_window_are_forgotten():
    c = SUP.Crashes()
    for t in (0.0, 1.0, 2.0, 3.0):
        c.crash(t)
    assert c.crash(3.0 + SUP.HOLD_WINDOW_S + 1) == 2.0    # the old four left the window
    assert not c.held


def test_the_backoff_is_capped():
    c = SUP.Crashes()
    assert c.delay(1) == 2.0 and c.delay(5) == 32.0
    assert c.delay(6) == SUP.BACKOFF_MAX_S and c.delay(20) == SUP.BACKOFF_MAX_S


# ---- flag off: nothing changes --------------------------------------------------------------

def test_with_the_flag_off_the_seat_runs_the_loop_in_process(monkeypatch):
    monkeypatch.setenv(SUP.FLAG, "0")            # #440 part 5: unset now means on; "0" is off
    srv = _felt()
    started = []
    monkeypatch.setattr(SL, "start", lambda s: started.append(s))
    assert SUP.enabled() is False
    assert SUP.boot(srv, "http://127.0.0.1:9") is None
    assert started == [srv] and SUP.of(srv) is None


def test_with_the_flag_on_the_seat_does_not_start_the_in_process_loop(monkeypatch):
    monkeypatch.setenv(SUP.FLAG, "1")
    srv = _felt()
    SL.set_mode(srv, "off")
    started = []
    monkeypatch.setattr(SL, "start", lambda s: started.append(s))
    sup = SUP.boot(srv, "http://127.0.0.1:9")
    try:
        assert started == [] and SUP.of(srv) is sup
        assert sup.diag()["state"] == "off"                # mode off: no process
    finally:
        SUP.close(srv)
    assert SUP.of(srv) is None


# ---- forwarding -----------------------------------------------------------------------------

class _Stub:
    """A child's control server: answers what it is told, records what it gets."""

    def __init__(self):
        self.got, self.delay, self.card = [], 0.0, {"name": "Blurp", "mode": "on", "level": 3}
        stub = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _out(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                time.sleep(stub.delay)
                stub.got.append(("GET", self.path, None))
                p = self.path.partition("?")[0]
                self._out({"/card": stub.card, "/sheet": {"header": {"level": 3}},
                           "/recent": {"lines": [{"kind": "cycle"}]},
                           "/status": {"mode": stub.card["mode"]}}.get(p, {"error": "nf"}))

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                b = json.loads(self.rfile.read(n) or b"{}")
                stub.got.append(("POST", self.path, b))
                if self.path == "/control":
                    applied = {k: b[k] for k in ("mode", "name") if k in b}
                    if "accept" in b:
                        applied["accept"] = {"ok": True, "id": b["accept"], "already": False}
                    if "mode" in b:
                        stub.card["mode"] = b["mode"]
                    return self._out({"ok": True, "applied": applied, "refused": []})
                return self._out({"ok": True})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@contextmanager
def _forwarding(srv):
    """A supervisor whose child is a stub (no process): the forwarding alone."""
    stub = _Stub()
    sup = SUP.Supervisor(srv, "http://127.0.0.1:9", autostart=False)
    sup._attach_port(stub.port)
    try:
        yield sup, stub
    finally:
        stub.close()
        sup.close()


def test_the_views_are_forwarded_to_the_child_and_the_last_good_card_is_kept(monkeypatch):
    with _open_env():
        srv = _felt()
        with _forwarding(srv) as (sup, stub):
            card = sup.view("/card")
            assert card["level"] == 3 and card["proc"]["state"] == "up"
            assert sup.view("/sheet") == {"header": {"level": 3}}
            assert sup.view("/recent", n=5) == {"lines": [{"kind": "cycle"}]}
            assert ("GET", "/recent?n=5", None) in stub.got
            assert sup.status()["mode"] == "on"
            monkeypatch.setattr(SUP, "FORWARD_TIMEOUT_S", 0.2)
            stub.delay = 1.0                                   # a slow child: the timeout, then the cache
            t0 = time.time()
            late = sup.view("/card")
            assert time.time() - t0 < 0.9
            assert late["level"] == 3 and late["proc"]["stale"] is True


def test_the_card_before_any_good_answer_is_the_seats_own_with_the_process_state():
    with _open_env():
        srv = _felt()
        sup = SUP.Supervisor(srv, "http://127.0.0.1:9", autostart=False)
        try:
            sup._crashes.held = True
            card = sup.view("/card")
            assert card["proc"]["state"] == "held" and card["proc"]["held"] is True
            assert "name" in card and "level" in card                    # the UI's fields
            assert sup.status()["proc"]["held"] is True
        finally:
            sup.close()


def test_the_seat_routes_forward_to_the_child(monkeypatch):
    with _open_env():
        srv = _felt()
        with _forwarding(srv) as (sup, stub), _served(srv) as url:
            port = int(url.rsplit(":", 1)[1])
            code, card = _call(port, "/membrane/self/card")
            assert code == 200 and card["proc"]["state"] == "up" and card["level"] == 3
            code, rec = _call(port, "/membrane/self/recent?n=7")
            assert code == 200 and rec == {"lines": [{"kind": "cycle"}]}
            assert ("GET", "/recent?n=7", None) in stub.got
            assert _call(port, "/membrane/self/sheet")[1] == {"header": {"level": 3}}


def test_the_panels_self_controls_go_to_the_child(monkeypatch):
    from membrane import seat_panel as PANEL
    with _open_env():
        srv = _felt()
        with _forwarding(srv) as (sup, stub):
            before = (SL.record_path(srv).read_text(), (SL._dir(srv) / "mode.json").read_text())
            out = PANEL.control(srv, {"self": "watch", "accept": "feel", "self_name": "Zed"}, by="tester")
            posts = [b for m, p, b in stub.got if m == "POST" and p == "/control"]
            assert posts and posts[0]["mode"] == "watch" and posts[0]["accept"] == "feel"
            assert "limit" not in posts[0] and posts[0]["name"] == "Zed" and posts[0]["by"] == "tester"
            assert out["applied"]["self"] == "watch" and out["applied"]["accept"]["ok"] is True
            assert out["applied"]["self_name"] == "Zed"
            assert (SL.record_path(srv).read_text(), (SL._dir(srv) / "mode.json").read_text()) == before
            assert sup.mode == "watch"                                     # the seat knows what it supervises


def test_a_person_speaking_reaches_the_child(monkeypatch):
    from membrane import energy as EN
    with _open_env():
        srv = _felt()
        with _forwarding(srv) as (sup, stub):
            EN.person_spoke(srv, "p1")
            deadline = time.time() + 5
            while time.time() < deadline and not [g for g in stub.got if g[1] == "/chat-in"]:
                time.sleep(0.02)
            assert ("POST", "/chat-in", {"person": "p1"}) in stub.got


def test_with_no_child_the_seat_applies_the_control_itself_and_a_switch_on_spawns(monkeypatch):
    with _open_env():
        srv = _felt()
        SL.set_mode(srv, "off")
        spawned = []
        sup = SUP.Supervisor(srv, "http://127.0.0.1:9", autostart=False)
        monkeypatch.setattr(sup, "_kick", lambda: spawned.append(sup.wanted()))
        try:
            code, out = sup.control({"mode": "on", "by": "tester"})
            assert code == 200 and out["applied"]["mode"] == "on"
            assert json.loads((SL._dir(srv) / "mode.json").read_text())["mode"] == "on"
            assert spawned == [True] and sup.mode == "on"
        finally:
            sup.close()


# ---- a real child, supervised -----------------------------------------------------------------

def _wait(cond, s=60.0):
    end = time.time() + s
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


@contextmanager
def _supervised(srv, url, tmp, **kw):
    env = _child_env(tmp)
    sup = SUP.Supervisor(srv, url, env=env, log_path=tmp / "self-proc.log", **kw)
    try:
        yield sup
    finally:
        sup.close()
        kid = sup._kid
        assert kid is None or kid.returncode is not None


def test_a_supervised_child_runs_restarts_after_a_crash_and_stops_when_switched_off(tmp_path):
    with _open_env(), mock.patch.dict(os.environ, {SUP.FLAG: "1"}):
        srv = _felt()
        with _served(srv) as url, _supervised(srv, url, tmp_path) as sup:
            assert _wait(lambda: sup.diag()["state"] == "up"), sup.diag()
            pid = sup.diag()["pid"]
            assert os.getpriority(os.PRIO_PROCESS, pid) >= SP.NICE
            assert sup.view("/card")["proc"]["state"] == "up"
            os.kill(pid, signal.SIGKILL)                               # a crash
            assert _wait(lambda: sup.diag()["state"] == "up" and sup.diag()["pid"] != pid), sup.diag()
            d = sup.diag()
            assert d["restarts"] == 1 and d["crashes"] == 1
            code, out = sup.control({"mode": "off"})
            assert code == 200
            assert _wait(lambda: sup.diag()["state"] == "off", 30), sup.diag()
            assert json.loads((SL._dir(srv) / "mode.json").read_text())["mode"] == "off"


def test_a_child_that_keeps_crashing_is_held_and_the_card_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(SUP, "BACKOFF_START_S", 0.05)
    with _open_env(), mock.patch.dict(os.environ, {SUP.FLAG: "1"}):
        srv = _felt()
        env = _child_env(tmp_path)
        env.pop(SP.FLAG)                                       # the child refuses at once: a crash each time
        sup = SUP.Supervisor(srv, "http://127.0.0.1:9", env=env, log_path=tmp_path / "self-proc.log")
        try:
            assert _wait(lambda: sup.diag()["held"], 120), sup.diag()
            d = sup.diag()
            assert d["state"] == "held" and d["crashes"] == SUP.HOLD_CRASHES and d["pid"] is None
            card = sup.view("/card")
            assert card["proc"]["held"] is True
        finally:
            sup.close()


def test_a_child_that_ignores_sigterm_is_killed_after_the_grace(tmp_path):
    sup = SUP.Supervisor(None, "http://127.0.0.1:9", autostart=False)
    code = "import signal,sys,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nsys.stdout.write('x')\n" \
           "sys.stdout.flush()\ntime.sleep(60)\n"
    r, w = os.pipe()
    pid = os.posix_spawn(sys.executable, [sys.executable, "-c", code], dict(os.environ),
                         file_actions=[(os.POSIX_SPAWN_DUP2, w, 1)])
    os.close(w)
    try:
        os.read(r, 1)                                           # it has set its handler
        kid = SP.Child(pid, None)
        sup._watch(kid)
        t0 = time.time()
        sup._stop_kid(kid, grace=0.5)
        assert kid.returncode is not None and time.time() - t0 < 5
        assert kid.returncode == -signal.SIGKILL
    finally:
        os.close(r)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        sup.close()





def test_blurps_own_move_goes_to_the_child_and_the_seat_writes_nothing_under_self():
    '''#740 (#440's one writer): with a child up, the reach line is the child's to write.'''
    from membrane import ch_blurp as B
    with _open_env():
        srv = _felt()
        with _forwarding(srv) as (sup, stub):
            before = SL.record_path(srv).read_text()
            B._reached(srv, 'w:b')
            deadline = time.time() + 5
            while time.time() < deadline and not [g for g in stub.got if g[1] == '/reach']:
                time.sleep(0.02)
            posts = [b for m, p, b in stub.got if m == 'POST' and p == '/reach']
            assert posts and posts[0]['eid'] == 'w:b' and posts[0]['fid']
            assert SL.record_path(srv).read_text() == before


def test_with_no_child_the_seat_writes_the_reach_line_itself():
    from membrane import ch_blurp as B
    with _open_env():
        srv = _felt()
        sup = SUP.Supervisor(srv, 'http://127.0.0.1:9', autostart=False)
        try:
            assert sup.reach('w:b', 'w:b') is False
            B._reached(srv, 'w:b')
            assert [r['eid'] for r in map(json.loads, SL.record_path(srv).read_text().splitlines())
                    if r.get('kind') == 'reach'] == ['w:b']
        finally:
            sup.close()
