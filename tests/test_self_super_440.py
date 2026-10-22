"""#440 part 4: THE SEAT SUPERVISES BLURP'S PROCESS (membrane.self_super).

Behind MEMBRANE_SELF_PROC=1 (default off: the seat runs the in-process loop
as before). With the flag on the seat does not start the in-process loop: a
supervisor spawns membrane.self_proc only while Blurp's mode is on or watch,
restarts it on a crash (2 s, doubling, capped at 60 s), holds it off after 5
crashes in 10 minutes (shown on the card and the panel's status), stops it
with SIGTERM then SIGKILL after 10 s, and forwards /membrane/self/* and the
self controls to its loopback port (5 s, then the last good answer).

The reply checks and the mirror feed (meet, turns, expect, mirror; speak's
reply checks) read the seat through the boundary too: a `facts` effect (a
thing's heard_ts and llm_answer, the mirror toy's name), `ask` (the model)
and `mirror_feed`, so they run the same in the child.

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

from membrane import self_api as API
from membrane import self_loop as SL
from membrane import self_proc as SP
from membrane import self_super as SUP
from membrane import toybox as TB

from tests.test_nudge_333 import T0, _cid, _felt, _lines  # noqa: F401
from tests.test_nudge_333 import _data  # noqa: F401  autouse fixture
from tests.test_self_proc_440 import _call, _child_env, _facade, _open_env, _served


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
    monkeypatch.delenv(SUP.FLAG, raising=False)
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
                    if "limit" in b:
                        applied["limit"] = 3
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
            out = PANEL.control(srv, {"self": "watch", "accept": "feel", "self_limit": 3, "self_name": "Zed"},
                                by="tester")
            posts = [b for m, p, b in stub.got if m == "POST" and p == "/control"]
            assert posts and posts[0]["mode"] == "watch" and posts[0]["accept"] == "feel"
            assert posts[0]["limit"] == 3 and posts[0]["name"] == "Zed" and posts[0]["by"] == "tester"
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
            assert _wait(lambda: len([r for r in _lines(srv) if r.get("kind") == "cycle"]) >= 1)
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


# ---- the reply checks and the mirror feed, through the boundary ---------------------------------

def test_the_reply_checks_read_the_seats_chat_thing_through_the_boundary(monkeypatch):
    with _open_env():
        srv = _felt()
        chat = _cid("chat:p1")
        from membrane.entities import QuantumEntity
        srv.entity_store.upsert(QuantumEntity(eid=chat, cls="Component", properties={
            "name": "p1", "heard_ts": 1050.0,
            "llm_answer": {"ts": 1010.0, "text": "VERDICT: 0.7", "model": "m1"}}), embedder=None)
        with _served(srv) as url:
            fac = _facade(url, srv)
            push = {"ts": 1000.0, "to": chat}
            assert SL._replied(fac, push, 1100.0, "unchanged") is True     # heard after the push, in the seat
            assert SL._last_spoke(fac, [], chat, 1100.0) == 1050.0
            assert SL._llm_pred(fac, chat, 1000.0) == pytest.approx(0.7)
            assert SL._llm_model(fac, chat) == "m1"


def test_the_mirror_toy_and_its_feed_are_the_seats(monkeypatch):
    with _open_env():
        srv = _felt(toys=("ball", "mirror"))
        name = SL._mirror_toy(srv)
        assert name is not None
        with _served(srv) as url:
            fac = _facade(url, srv)
            assert SL._mirror_toy(fac) == name                  # the seat's things, not the child's mirror
            fed = []
            real = TB.mirror_feed
            monkeypatch.setattr(TB, "mirror_feed", lambda n, s, now=None: (fed.append(n), real(n, s, now=now))[1])
            out = API.effects(fac, [{"kind": "mirror_feed", "toy": name, "s": 0.1, "now": T0}])[0]
            assert out["ok"] is True and fed == [name]


def test_the_model_is_asked_in_the_seat(monkeypatch):
    from membrane import llm as LLM
    with _open_env():
        srv = _felt()
        asked = []
        monkeypatch.setattr(LLM, "request", lambda s, prompt, **kw: (asked.append((s, prompt, kw)),
                                                                      {"rc": 0, "text": "queued"})[1])
        with _served(srv) as url:
            fac = _facade(url, srv)
            out = SL._ask_llm(fac, _cid("chat:p1"), "will they reply?")
        assert out == {"rc": 0, "text": "queued"}
        assert asked and asked[0][0] is srv and asked[0][1] == "will they reply?"
        assert asked[0][2]["about"] == _cid("chat:p1") and asked[0][2]["caller"] == "self"
