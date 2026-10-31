"""#440 part 2: THE SEAT'S TWO ROUTES FOR BLURP'S OWN PROCESS.

Blurp runs in a child process (#440). The child reaches the seat through two
routes (the in-process loop crosses the same boundary through the same code):

- GET /membrane/self/context: the small snapshot of seat state a step
  reads -- Blurp's own entity, the pause, the CPU share; on request Blurp's
  own entities (to seed the child's mirror). Never more than the approved
  context budget, 256 KB.
- POST /membrane/self/effects: the acts, run in the seat in order, each with
  its result -- today `own` (Blurp's own properties). #576 retired the old
  learner's reads and acts (the current, the things and spots, nudge, say).

Both are for the seat's own token (or open mode): a person's token is refused."""
import json
import os
import tempfile
import threading
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import pytest

from membrane import self_api as API
from membrane import self_loop as SL
from membrane.entities import EntityStore, QuantumEntity

SECRET = "s" * 40


def _seat():
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.efield = None
    srv.events_pool = None
    srv.membrane = None
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


def _size(doc) -> int:
    return len(json.dumps(doc).encode())


def _roundtrip(x):
    return json.loads(json.dumps(x))


def test_the_context_budget_is_the_approved_256_kb():
    assert API.CONTEXT_BUDGET == 256 * 1024


# ---- the context ----------------------------------------------------------------------------

def test_the_context_is_small_json_with_what_a_step_reads():
    srv = _seat()
    ctx = API.context(srv)
    assert ctx == _roundtrip(ctx)                                  # plain JSON, nothing else
    assert _size(ctx) <= API.CONTEXT_BUDGET
    assert ctx["self"]["eid"] == SL._eid()
    assert ctx["paused"] is False and ctx["nerve_paused"] is False
    assert ctx["cpu_share"] == pytest.approx(SL.cpu_share(srv))
    assert "own" not in ctx and "current" not in ctx and "things" not in ctx


def test_the_context_says_paused():
    srv = _seat()
    srv.paused = True
    ctx = API.context(srv)
    assert ctx["paused"] is True and ctx["nerve_paused"] is True


def test_own_gives_blurps_own_entities_only():
    srv = _seat()
    srv.entity_store.upsert(QuantumEntity(eid="membrane::Component:toy:x", cls="Component",
                                          properties={"name": "x"}), embedder=None)
    got = API.context(srv, own=True, only="own")
    assert set(got) <= {"v", "ts", "own"}
    assert set(got["own"]) == {SL._eid()}


def test_an_answer_over_the_budget_says_so(monkeypatch):
    srv = _seat()
    monkeypatch.setattr(API, "_own", lambda srv: {"x": "y" * (API.CONTEXT_BUDGET + 1)})
    ctx = API.context(srv, own=True)
    assert ctx.get("over_budget") is True and "own" not in ctx
    assert _size(ctx) <= API.CONTEXT_BUDGET


# ---- the effects ----------------------------------------------------------------------------

def test_an_own_effect_writes_blurps_own_properties_and_never_anothers():
    srv = _seat()
    out = API.effects(srv, [{"kind": "own", "eid": SL._eid(), "properties": {"script_data": {"a": 1}}}])
    assert out == [{"ok": True}]
    assert srv.entity_store.docs[SL._eid()].properties["script_data"]["a"] == 1
    other = "membrane::Component:toy:x"
    srv.entity_store.upsert(QuantumEntity(eid=other, cls="Component", properties={"name": "x"}), embedder=None)
    assert API.effects(srv, [{"kind": "own", "eid": other, "properties": {"name": "y"}}])[0]["ok"] is False


def test_an_unknown_effect_is_refused_and_a_bad_list_raises():
    srv = _seat()
    for kind in ("explode", "position", "nudge", "say"):              # #576: the old learner's acts are gone
        assert API.effects(srv, [{"kind": kind}])[0]["ok"] is False
    with pytest.raises(ValueError):
        API.effects(srv, {"kind": "own"})


# ---- the routes ----------------------------------------------------------------------------

def _jwt_env(**kw):
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    base.update(kw)
    if any(k in base for k in ('MEMBRANE_JWT_SECRET', 'MEMBRANE_JWT_SECRET_FILE')):
        base.pop('MEMBRANE_JWT', None)   # explicit secret = deliberate 'on' (#762)
    else:
        base['MEMBRANE_JWT'] = '0'       # otherwise the suite's default: off
    return mock.patch.dict(os.environ, base, clear=True)


@pytest.fixture
def route():
    from membrane import server as NS
    from tests.test_server import _CFG, _StubRunner, _store
    srv = _seat()
    runner = _StubRunner()
    runner.srv = srv
    handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                              config=_CFG, runner=runner)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(path, body=None, tok=None):
        h = {"Content-Type": "application/json"}
        if tok:
            h["Authorization"] = f"Bearer {tok}"
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", data=data, headers=h,
                                     method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, None
    yield srv, call
    httpd.shutdown()
    httpd.server_close()


def test_get_context_serves_the_snapshot(route):
    srv, call = route
    with _jwt_env():
        code, body = call("/membrane/self/context")
        assert code == 200 and body["self"]["eid"] == SL._eid()
        code, body = call("/membrane/self/context?only=own&own=1")
        assert code == 200 and SL._eid() in body["own"] and "self" not in body


def test_post_effects_runs_them_in_the_seat(route):
    srv, call = route
    with _jwt_env():
        code, body = call("/membrane/self/effects",
                          {"effects": [{"kind": "own", "eid": SL._eid(), "properties": {"script_data": {"b": 2}}}]})
        assert code == 200 and body["results"][0] == {"ok": True}
        assert srv.entity_store.docs[SL._eid()].properties["script_data"]["b"] == 2
        assert call("/membrane/self/effects", {"effects": "no"})[0] == 400


def test_a_persons_token_gets_neither(route):
    from membrane import jwtauth as J
    srv, call = route
    with _jwt_env(MEMBRANE_JWT_SECRET=SECRET):
        assert call("/membrane/self/context")[0] == 401
        alice = J.mint(SECRET, "alice")
        assert call("/membrane/self/context", tok=alice)[0] == 403
        assert call("/membrane/self/effects", {"effects": []}, tok=alice)[0] == 403


def test_get_context_never_spends_the_report(route):
    """#586: the learn exchange mutates the purse, so it rides a POST; a GET
    carrying r_* params is a pure read and applies nothing."""
    srv, call = route
    seen = []
    from membrane import self_api as SA
    orig = SA.context
    SA.context = lambda s, **kw: (seen.append(kw.get("report")) or orig(s, **kw))
    try:
        with _jwt_env():
            assert call("/membrane/self/context?r_awake=1")[0] == 200
            assert call("/membrane/self/context", {"report": {"awake": True}})[0] == 200
    finally:
        SA.context = orig
    assert seen == [None, {"awake": True}]
