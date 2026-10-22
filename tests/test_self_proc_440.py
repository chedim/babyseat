"""#440 part 3: BLURP'S LOOP IN ITS OWN PROCESS (membrane.self_proc).

The child runs the same self_loop code against a facade of the seat: an
srv-shaped object whose entity store is a local mirror of Blurp's own
entities (itself and its methods), whose reads of the seat are
GET /membrane/self/context and whose acts are POST /membrane/self/effects.
The facade covers what part 2 left in-process: a look (the getter read runs
in the seat: the `reach` effect), Blurp's own method runs and properties
(the `own` effect, and `own` in the context to seed the mirror), the CPU
share and the pause (read from the context), and the side acts of a cycle
(placing a looked-at thing, provisioning unlocked toys, the audits' getter
pause, the viewer). It owns self/ and serves its card / sheet / recent /
status / control / chat-in on a loopback port it reports at start.

Behind MEMBRANE_SELF_PROC=1 (default off): in this part the seat does not
start the child -- a test (or part 4's supervisor) does. Tests that start a
real child clean it up on every path and keep it inside tmp_path."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from membrane import self_api as API
from membrane import self_loop as SL
from membrane import self_proc as SP
from membrane import toybox as TB
from membrane import watch_agents as WA

from tests.test_nudge_333 import DIM, T0, _cid, _felt, _lines  # noqa: F401
from tests.test_nudge_333 import _data  # noqa: F401  autouse fixture
from tests import test_self_rung3 as R3

BALL = _cid("toy:ball")
SECRET = "s" * 40


def _open_env():
    """Open mode (no JWT secret), whatever the developer's shell holds."""
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    return mock.patch.dict(os.environ, base, clear=True)


@contextmanager
def _served(srv):
    """The seat's HTTP API over `srv`, on a loopback port (the real handler)."""
    from membrane import server as NS
    from tests.test_server import _CFG, _StubRunner, _store
    runner = _StubRunner()
    runner.srv = srv
    handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                              config=_CFG, runner=runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _facade(url, srv):
    """The child's view of `srv`'s seat (same data dir, as on a seat)."""
    fac = SP.Facade(SP.SeatClient(url), Path(srv._models_path).parent)
    SP.bind(fac)
    iso = SL._rt(srv).get("isolate")
    if iso is not None:
        SL._rt(fac)["isolate"] = set(iso)
    return fac


# ---- the approved constants ---------------------------------------------------------------

def test_the_child_uses_the_approved_constants():
    assert SP.NICE == 10                 # #440 constant 1
    assert SP.STOP_GRACE_S == 10.0       # #440 constant 4
    assert SP.TIMEOUT_S == 5.0           # #440 constant 5 (each HTTP hop)
    assert SP.FLAG == "MEMBRANE_SELF_PROC"


# ---- the new seat-side effects and the context's own part ------------------------------------

def test_the_context_carries_blurps_own_entities_on_request():
    srv = _felt()
    got = API.context(srv, own=True, only="own")
    assert set(got) <= {"v", "ts", "own", "spots", "dropped"}
    own = got["own"]
    me = SL._eid()
    assert me in own
    meths = {(d["properties"] or {}).get("method_name") for e, d in own.items() if e != me}
    assert set(SL.METHODS) <= meths
    assert all(me in (d["properties"].get("method_of") or [me]) for d in own.values())
    assert BALL not in own                                      # never another thing
    assert got == json.loads(json.dumps(got))
    assert "own" not in API.context(srv)                         # only when asked


def test_the_context_says_whether_a_viewer_watches():
    srv = _felt()
    assert API.context(srv)["viewer"] is False
    srv._field_pulse = object()
    assert API.context(srv)["viewer"] is True


def test_an_own_effect_updates_blurps_own_entities_and_nothing_else():
    srv = _felt()
    me = SL._eid()
    meth = next(e for e, d in srv.entity_store.docs.items()
                if (d.properties or {}).get("method_name") == "try" and me in d.properties.get("method_of", []))
    srv.entity_store.docs[me].properties.setdefault("script_data", {})["pull"] = 0.5
    out = API.effects(srv, [{"kind": "own", "eid": me, "properties": {"script_data": {"kinds": {"x": "moving"}}}},
                            {"kind": "own", "eid": meth, "properties": {"runs": 7, "ok_runs": 6}},
                            {"kind": "own", "eid": BALL, "properties": {"name": "stolen"}}])
    assert [o["ok"] for o in out] == [True, True, False]
    sd = srv.entity_store.docs[me].properties["script_data"]
    assert sd["kinds"] == {"x": "moving"} and sd["pull"] == 0.5      # merged, not replaced
    assert srv.entity_store.docs[meth].properties["runs"] == 7
    assert srv.entity_store.docs[BALL].properties["name"] != "stolen"


def test_a_reach_effect_reads_the_thing_in_the_seat(monkeypatch):
    srv = _felt()
    seen = []

    def read(s, ent, now):
        seen.append((s, ent.eid, now))
        return {"fired": True, "state": {"v": 1}, "changed": True}
    monkeypatch.setattr(SL, "_read_entity", read)
    out = API.effects(srv, [{"kind": "reach", "eid": BALL, "now": T0},
                            {"kind": "reach", "eid": _cid("toy:none")}])
    assert seen == [(srv, BALL, T0)]
    assert out[0]["read"]["changed"] is True and out[0]["toy"]["name"] == "toy:ball"
    assert out[0]["toy"]["at1"] >= out[0]["toy"]["at0"] and "by" in out[0]["toy"]
    assert out[1]["read"] == {"fired": False, "reason": "gone"}
    assert out == json.loads(json.dumps(out))


def test_the_side_effects_run_in_the_seat():
    srv = _felt()
    out = API.effects(srv, [{"kind": "getter", "eid": BALL, "until": time.time() + 60}])
    assert out[0]["ok"] is True and BALL in WA._getter_paused
    API.effects(srv, [{"kind": "getter", "eid": BALL, "until": None}])
    assert BALL not in WA._getter_paused
    got = API.effects(srv, [{"kind": "place", "eid": BALL}])[0]
    assert got["placed"] in (True, False)                        # placed, or already there
    assert API.effects(srv, [{"kind": "provision", "toys": [], "now": T0}])[0] == {"added": []}
    calls = []
    srv._field_pulse = type("FP", (), {"self_cycle": lambda self, p, s: calls.append((p, s))})()
    API.effects(srv, [{"kind": "show", "points": {"from": [1.0, 0.0], "aim": None}, "stats": {"n": 3}}])
    assert calls and list(calls[0][0]["from"]) == [1.0, 0.0] and calls[0][0]["aim"] is None
    assert calls[0][1] == {"n": 3}


# ---- the facade: the same loop, across the boundary -------------------------------------------

def test_the_facade_holds_only_the_listed_seat_parts():
    with _open_env():
        srv = _felt()
        with _served(srv) as url:
            fac = _facade(url, srv)
            assert set(SP.FACADE_ATTRS) == {"entity_store", "_models_path", "self_remote", "paused",
                                             "efield", "events_pool", "membrane"}
            for a in SP.FACADE_ATTRS:
                getattr(fac, a)
            with pytest.raises(AttributeError):
                fac.anything_else
            assert fac.efield is None                            # the field is the seat's
            assert set(fac.entity_store.docs) == set(API.context(srv, own=True, only="own")["own"])


def _same(r):
    return {k: v for k, v in r.items() if k not in ("dur_s", "ts")}


def test_the_child_runs_the_same_cycles_as_the_seat(monkeypatch, tmp_path):
    """Two seats built alike, each in its own data dir: one runs its loop
    in-process, the other's runs on the facade over HTTP. The records agree
    line for line, and the child's acts land in its seat."""
    monkeypatch.setattr(SL, "AIM_HOLD", 2 * 11.0)
    with _open_env():
        monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "a"))
        a = _felt()
        for k in range(30):
            SL.cycle(a, now=T0 + 100 + 11 * k)
        mine = [_same(r) for r in _lines(a)]
        monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "b"))
        b = _felt()
        with _served(b) as url:
            fac = _facade(url, b)
            for k in range(30):
                SL.cycle(fac, now=T0 + 100 + 11 * k)
                fac.flush()
            theirs = [_same(r) for r in _lines(b)]
            assert theirs == mine
            assert len([r for r in theirs if r["kind"] == "cycle"]) == 30
            assert [r for r in theirs if r["kind"] == "nudge"]
            pos = b.entity_store.docs[SL._eid()].position                    # its move reached the seat
            assert list(pos) == pytest.approx(list(fac.entity_store.docs[SL._eid()].position))
            runs = {(d.properties or {}).get("method_name"): (d.properties or {}).get("runs")
                    for d in b.entity_store.docs.values() if SL._eid() in ((d.properties or {}).get("method_of") or [])}
            assert runs["try"] == 30 and runs["expect"] == 30          # its own method runs, in the seat
            assert TB.peek("toy:ball") is not None


def test_the_child_looks_through_the_seat(monkeypatch):
    """Rung 2-4's looks: the getter fires in the seat (the reach effect), the
    tempo and the record are the child's; the same answers either way."""
    with _open_env():
        monkeypatch.setattr(SL, "_read_entity", R3._Things())
        a = R3._seat(names=("always", "never", "half"))
        R3._run(a, 12)
        mine = [(r["eid"], r["answer"]) for r in R3._reaches(a)]
        monkeypatch.setattr(SL, "_read_entity", R3._Things())
        b = R3._seat(names=("always", "never", "half"))
        seat_reads = []
        real = API._reach_effect

        def spy(srv, e):
            seat_reads.append(srv)
            return real(srv, e)
        monkeypatch.setitem(API.EFFECTS, "reach", spy)
        with _served(b) as url:
            fac = _facade(url, b)
            R3._run(fac, 12)
        theirs = [(r["eid"], r["answer"]) for r in R3._reaches(b)]
        assert theirs == mine and len(mine) == 12
        assert seat_reads and all(s is b for s in seat_reads)        # every look ran in the seat


def test_the_facade_reads_the_pause_and_the_share_from_the_context(monkeypatch):
    from membrane import options as O
    with _open_env():
        srv = _felt()
        with _served(srv) as url:
            fac = _facade(url, srv)

            def share(s=None):
                assert s is srv, "the child never reads the seat's settings itself"
                return 0.25
            monkeypatch.setattr(O, "cpu_share", share)
            assert SL.cpu_share(fac) == 0.25                     # the seat's, through its context
            srv.paused = True
            assert SL._paused(fac) is True
            assert SL.step(fac, now=T0 + 100) is None            # the start check: paused, nothing runs
            assert not [r for r in _lines(srv) if r.get("kind") == "cycle"]
            srv.paused = False
            assert SL._paused(fac) is False


def test_the_facade_never_writes_the_seats_store_directly():
    with _open_env():
        srv = _felt()
        with _served(srv) as url:
            fac = _facade(url, srv)
            assert fac.entity_store is not srv.entity_store
            assert fac.entity_store.path is None and fac.entity_store.shared is False


# ---- the control server ----------------------------------------------------------------------

def _call(port, path, body=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=h,
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, None


def test_the_control_server_serves_blurps_views_and_takes_its_controls(monkeypatch):
    with _open_env():
        srv = _felt()
        with _served(srv) as url:
            fac = _facade(url, srv)
            httpd = SP.serve(fac)
            try:
                port = httpd.server_address[1]
                assert httpd.server_address[0] == "127.0.0.1"
                for p in ("/card", "/sheet", "/recent", "/status"):
                    code, body = _call(port, p)
                    assert code == 200 and body is not None, p
                assert _call(port, "/status")[1]["mode"] == "on"
                code, body = _call(port, "/control", {"mode": "watch"})
                assert code == 200 and SL.mode(fac) == "watch"
                assert json.loads((Path(srv._models_path).parent / "self" / "mode.json").read_text())["mode"] == "watch"
                spoke = []
                from membrane import energy as EN
                monkeypatch.setattr(EN, "person_spoke", lambda s, person, **kw: spoke.append((s, person)))
                assert _call(port, "/chat-in", {"person": "p1"})[0] == 200
                assert spoke == [(fac, "p1")]
                assert _call(port, "/control", {"mode": "nonsense"})[0] == 400
                assert _call(port, "/nowhere")[0] == 404
            finally:
                httpd.shutdown()
                httpd.server_close()


def test_the_control_server_takes_only_the_seats_own_token():
    from membrane import jwtauth as J
    with _open_env(), mock.patch.dict(os.environ, {"MEMBRANE_JWT_SECRET": SECRET}):
        srv = _felt()
        fac = SP.Facade(SP.SeatClient("http://127.0.0.1:9"), Path(srv._models_path).parent)
        SL._rt(fac)["mode"] = "on"
        httpd = SP.serve(fac)
        try:
            port = httpd.server_address[1]
            assert _call(port, "/status")[0] == 401
            assert _call(port, "/status", tok=J.mint(SECRET, "alice"))[0] == 403
            assert _call(port, "/status", tok=J.internal_token())[0] == 200
        finally:
            httpd.shutdown()
            httpd.server_close()


# ---- the real child --------------------------------------------------------------------------

def _child_env(tmp):
    """A child's env: open mode, everything it may write inside tmp."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("MEMBRANE_") and k not in ("PYTHONSTARTUP",)}
    env.update({"MEMBRANE_DATA_DIR": str(tmp / "data"), "MEMBRANE_STORE": "json", "MEMBRANE_RUN": "0",
                "HOME": str(tmp / "home"), "PYTHONDONTWRITEBYTECODE": "1", SP.FLAG: "1",
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent)})
    (tmp / "home").mkdir(exist_ok=True)
    return env


@contextmanager
def _child(url, tmp, env=None):
    kid = SP.spawn(url, tmp / "data", env=env or _child_env(tmp), log_path=tmp / "self-proc.log", wait_s=60.0)
    try:
        yield kid
    finally:
        SP.stop(kid)
        assert kid.exited()


def test_the_child_entry_refuses_without_the_flag(tmp_path):
    env = _child_env(tmp_path)
    env.pop(SP.FLAG)
    with pytest.raises(SP.SpawnError):
        with _child("http://127.0.0.1:9", tmp_path, env=env):
            pass
    assert "MEMBRANE_SELF_PROC" in (tmp_path / "self-proc.log").read_text()


def test_a_real_child_runs_blurp_and_serves_its_port(tmp_path):
    with _open_env():
        srv = _felt()
        assert Path(srv._models_path).parent == tmp_path / "data"
        with _served(srv) as url, _child(url, tmp_path) as kid:
            assert kid.pid > 0 and kid.port > 0
            assert os.getpriority(os.PRIO_PROCESS, kid.pid) >= SP.NICE   # the OS favours the seat
            code, st = _call(kid.port, "/status")
            assert code == 200 and st["mode"] == "on"
            deadline = time.time() + 60
            while time.time() < deadline:
                cyc = [r for r in _lines(srv) if r.get("kind") == "cycle"]
                if len(cyc) >= 2:
                    break
                time.sleep(0.2)
            assert len(cyc) >= 2                                 # the child's cycles, on the record
            runs = {(d.properties or {}).get("method_name"): (d.properties or {}).get("runs")
                    for d in srv.entity_store.docs.values() if (d.properties or {}).get("method_of")}
            assert (runs.get("try") or 0) >= 1                   # its own method runs reached the seat
            assert "ok" in _call(kid.port, "/control", {"mode": "off"})[1]


def test_the_child_exits_when_its_stdin_closes(tmp_path):
    with _open_env():
        srv = _felt()
        with _served(srv) as url, _child(url, tmp_path) as kid:
            kid.close_stdin()
            deadline = time.time() + 30
            while time.time() < deadline and not kid.exited():
                time.sleep(0.1)
            assert kid.exited() and kid.returncode == 0


def test_the_child_stops_on_sigterm(tmp_path):
    with _open_env():
        srv = _felt()
        with _served(srv) as url, _child(url, tmp_path) as kid:
            t0 = time.time()
            SP.stop(kid)
            assert kid.exited() and time.time() - t0 < SP.STOP_GRACE_S
            assert kid.returncode == 0                           # its own clean exit, not the SIGKILL
