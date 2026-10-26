"""#440 part 3: BLURP'S LOOP IN ITS OWN PROCESS (membrane.self_proc).

The child runs the same self_loop code against a facade of the seat: an
srv-shaped object whose entity store is a local mirror of Blurp's own
entities, whose reads of the seat are GET /membrane/self/context and whose
acts are POST /membrane/self/effects. The facade covers Blurp's own
properties (the `own` effect, and `own` in the context to seed the mirror),
the CPU share and the pause (read from the context). #576 retired the old
learner's looks and acts. It owns self/ and serves its card / sheet / recent
/ status / control / chat-in on a loopback port it reports at start.

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
from membrane.entities import EntityStore, QuantumEntity

T0 = 1_700_000_000.0
SECRET = "s" * 40
OTHER = "membrane::Component:toy:ball"


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    yield tmp_path / "data"


def _felt():
    """A seat whose Blurp is on, beside another thing (never Blurp's own)."""
    import types
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = None
    est._srv = srv
    est.upsert(QuantumEntity(eid=OTHER, cls="Component", properties={"name": "toy:ball"}), embedder=None)
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    SL.set_mode(srv, "on")
    return srv


def _lines(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


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
    assert set(got) <= {"v", "ts", "own"}
    own = got["own"]
    assert set(own) == {SL._eid()}                              # #576: no methods; never another thing
    assert got == json.loads(json.dumps(got))
    assert "own" not in API.context(srv)                         # only when asked


def test_an_own_effect_updates_blurps_own_entities_and_nothing_else():
    srv = _felt()
    me = SL._eid()
    srv.entity_store.docs[me].properties.setdefault("script_data", {})["pull"] = 0.5
    out = API.effects(srv, [{"kind": "own", "eid": me, "properties": {"script_data": {"kinds": {"x": "moving"}}}},
                            {"kind": "own", "eid": OTHER, "properties": {"name": "stolen"}}])
    assert [o["ok"] for o in out] == [True, False]
    sd = srv.entity_store.docs[me].properties["script_data"]
    assert sd["kinds"] == {"x": "moving"} and sd["pull"] == 0.5      # merged, not replaced
    assert srv.entity_store.docs[OTHER].properties["name"] != "stolen"


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
            ran = []
            monkeypatch.setattr(SL, "learn_step", lambda s, now: ran.append(now))
            assert SL.step(fac, now=T0 + 100) is None            # the start check: paused, nothing runs
            assert ran == []
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
            code, card = _call(kid.port, "/card")
            assert code == 200 and card["level"] == 0
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
