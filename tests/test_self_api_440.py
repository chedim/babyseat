"""#440 part 2: THE SEAT'S TWO ROUTES FOR BLURP'S OWN PROCESS.

Blurp is moving into a child process (#440). The child will reach the seat
through two routes, and this part adds them and makes the in-process loop
use them already (the same code the routes run), so part 3 can move the loop
out unchanged:

- GET /membrane/self/context: the small per-cycle snapshot of seat state
  the cycle reads -- the current, Blurp's own position, the things in its
  world (no vectors), the pause, the CPU share; on request the field spots
  it can reach (their dimensions) and the vectors of the spots it asks for.
  Never the whole field: one call stays under the approved context budget,
  256 KB, whatever the world holds (it is cut to fit, and says so).
- POST /membrane/self/effects: the cycle's acts, run in the seat in order --
  its position, nudges at other things, chat out -- each with its result.

Both are for the seat's own token (or open mode): a person's token is refused."""
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

import numpy as np
import pytest

from membrane import self_api as API
from membrane import self_loop as SL
from membrane import self_world as SW
from membrane import toybox as TB
from membrane.eids import field_id
from membrane.entities import QuantumEntity

from tests.test_nudge_333 import DIM, T0, _cid, _felt, _lines, _seat  # noqa: F401
from tests.test_nudge_333 import _data  # noqa: F401  autouse fixture
from tests.test_nudge_chat_out_333 import COUT, HEARD, MOVE, _chat_seat, _hear, _said, _thing  # noqa: F401

BALL = _cid("toy:ball")
DIE = _cid("toy:die")
SECRET = "s" * 40


def _size(doc) -> int:
    return len(json.dumps(doc).encode())


def _roundtrip(x):
    return json.loads(json.dumps(x))


# ---- the budget ---------------------------------------------------------------------------

def test_the_context_budget_is_the_approved_256_kb():
    assert API.CONTEXT_BUDGET == 256 * 1024


# ---- the context ----------------------------------------------------------------------------

def test_the_context_is_small_json_with_what_the_cycle_reads():
    srv = _felt()
    ctx = API.context(srv)
    assert ctx == _roundtrip(ctx)                                  # plain JSON, nothing else
    assert _size(ctx) <= API.CONTEXT_BUDGET
    assert ctx["self"]["eid"] == SL._eid()
    assert ctx["current"] == pytest.approx(list(SL._current(srv)))
    things = {t["eid"]: t for t in ctx["things"]}
    assert {BALL, DIE} <= set(things)                              # its world...
    assert all(SL._in_world(srv, e) for e in things)               # ...only its world
    assert SL._eid() not in things                                 # ...never itself
    assert ctx["paused"] is False and ctx["nerve_paused"] is False
    assert ctx["cpu_share"] == pytest.approx(SL.cpu_share(srv))
    assert "reach" not in ctx and ctx["spots"] == {}             # no vectors unless asked
    assert "dropped" not in ctx


def test_the_context_says_paused_and_marks_the_chat_thing_and_the_getters():
    srv, _ = _chat_seat()
    srv.paused = True
    ctx = API.context(srv)
    assert ctx["paused"] is True and ctx["nerve_paused"] is True
    things = {t["eid"]: t for t in ctx["things"]}
    assert things[COUT].get("chat") is True
    assert {e for e, t in things.items() if t.get("getter")} == set(SL._targets(srv))


def test_reach_gives_the_spots_dimensions_and_spots_the_asked_vectors():
    srv = _felt()
    outside = _cid("toy:elsewhere")
    srv.entity_store.upsert(QuantumEntity(eid=outside, cls="Component", properties={"name": "elsewhere"}),
                            embedder=None)
    TB.place(srv, outside)
    got = API.context(srv, spots=[BALL], reach=True, only="spots")
    assert set(got) <= {"v", "ts", "spots", "reach", "dropped"}  # only the spots part
    assert got["reach"] == {BALL: DIM, DIE: DIM}                   # placed and in its world; never the outside one
    assert got["spots"][BALL] == pytest.approx(list(SL._spot(srv, BALL)))
    bare = _cid("toy:bare")                                        # in its world, never placed
    srv.entity_store.upsert(QuantumEntity(eid=bare, cls="Component", properties={"name": "bare"}), embedder=None)
    SW.join(srv, [bare], "test")
    assert bare not in API.context(srv, reach=True, only="spots")["reach"]
    assert API.context(srv, spots=[bare], only="spots")["spots"] == {}   # no spot: nothing


def test_a_huge_world_is_cut_to_the_budget_and_says_so(monkeypatch):
    srv = _felt()
    many = [_cid(f"toy:thing-{i:05d}-" + "x" * 80) for i in range(4000)]
    for e in many:
        srv.entity_store.docs[e] = QuantumEntity(eid=e, cls="Component", properties={"name": e})
    SW.join(srv, many, "test")
    big = np.full(1024, 1.0 / 32.0)                                # a 1024-d field: ~20 KB a vector
    monkeypatch.setattr(SL, "_spot", lambda srv, eid: big if eid in set(many) else None)
    ctx = API.context(srv)
    assert _size(ctx) <= API.CONTEXT_BUDGET
    assert ctx["dropped"]["things"] > 0
    assert len(ctx["things"]) + ctx["dropped"]["things"] == len(many) + 2
    sp = API.context(srv, spots=many[:200], reach=True)
    assert _size(sp) <= API.CONTEXT_BUDGET
    assert sp["dropped"]["spots"] > 0 and len(sp["spots"]) + sp["dropped"]["spots"] == 200
    assert ctx["current"] is not None and ctx["self"]["eid"] == SL._eid()   # the cycle's own part is never cut


def test_a_small_world_is_never_cut():
    srv = _felt()
    TB.place(srv, BALL)
    assert "dropped" not in API.context(srv, spots=[BALL], reach=True)


# ---- the effects ----------------------------------------------------------------------------

def test_a_position_effect_moves_blurp_in_the_seat():
    srv = _felt()
    pos = list(np.ones(DIM) / np.sqrt(DIM))
    out = API.effects(srv, [{"kind": "position", "position": pos, "pull": 0.25}])
    assert out == [{"ok": True}]
    ent = srv.entity_store.docs[SL._eid()]
    assert list(ent.position) == pytest.approx(pos)
    assert ent.properties["script_data"]["pull"] == 0.25


def test_a_nudge_effect_runs_the_things_own_nudge_in_the_seat():
    srv = _felt()
    assert TB.peek("toy:ball")["side"] == "self"
    out = API.effects(srv, [{"kind": "nudge", "eid": BALL, "now": T0}])
    assert out[0]["rc"] == SL.NUDGE_MOVED and TB.peek("toy:ball")["side"] == "other"
    outside = _cid("toy:elsewhere")
    srv.entity_store.upsert(QuantumEntity(eid=outside, cls="Component", properties={"name": "x"}), embedder=None)
    got = API.effects(srv, [{"kind": "nudge", "eid": outside}, {"kind": "nudge", "eid": _cid("toy:none")}])
    assert [g["rc"] for g in got] == [SL.NUDGE_REFUSED, SL.NUDGE_NOTHING]   # in order, each its own result
    assert got == _roundtrip(got)


def test_a_say_effect_sends_the_line_that_fits_through_the_chat_thing():
    srv, r = _chat_seat()
    _hear(srv, HEARD[:3])
    lamp = _thing(srv, "lamp", HEARD[1])
    out = API.effects(srv, [{"kind": "say", "eid": COUT, "sum": list(SL._spot(srv, lamp))}])
    assert out[0]["posted"] is True and len(out[0]["said"]) == 1
    said = _said(r)
    assert len(said) == 1 and HEARD[1] in said[0][1] and said[0][2] == "self"


def test_an_unknown_effect_is_refused_and_a_bad_list_raises():
    srv = _felt()
    assert API.effects(srv, [{"kind": "explode"}])[0]["ok"] is False
    with pytest.raises(ValueError):
        API.effects(srv, {"kind": "position"})


# ---- the loop uses them -------------------------------------------------------------------

def _spy(monkeypatch):
    calls = {"context": [], "effects": []}
    ctx0, eff0 = API.context, API.effects

    def ctx(srv, *a, **kw):
        out = ctx0(srv, *a, **kw)
        assert out == _roundtrip(out) and _size(out) <= API.CONTEXT_BUDGET
        calls["context"].append((a, kw))
        return out

    def eff(srv, effects):
        assert effects == _roundtrip(effects)                      # the child can send exactly this
        out = eff0(srv, effects)
        assert out == _roundtrip(out)
        calls["effects"].extend(e["kind"] for e in effects)
        return out
    monkeypatch.setattr(API, "context", ctx)
    monkeypatch.setattr(API, "effects", eff)
    return calls


def test_the_cycle_reads_the_seat_through_context_and_acts_through_effects(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 2 * 11.0)
    srv = _felt()
    calls = _spy(monkeypatch)
    for k in range(40):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    cyc = [r for r in _lines(srv) if r.get("kind") == "cycle"]
    ns = [r for r in _lines(srv) if r.get("kind") == "nudge"]
    assert len(cyc) == 40 and ns
    assert calls["effects"].count("position") == len(cyc)          # each cycle's move, through the seat
    assert calls["effects"].count("nudge") == len(ns)              # each nudge at a toy, through the seat
    assert sum(1 for a, kw in calls["context"] if not kw.get("only")) == len(cyc)   # one snapshot a cycle


def test_the_cycle_speaks_through_the_say_effect(monkeypatch):
    monkeypatch.setattr(SL, "AIM_HOLD", 2 * 11.0)
    rec = [{"ts": T0, "kind": "birth", "by": "operator"},
           {"ts": T0 + 1, "kind": "achieve", "id": "feel", "rung": 1, "skill": 0.9}]
    srv, r = _chat_seat(record=rec)
    _hear(srv, HEARD)
    _thing(srv, "lamp", HEARD[2])
    SL.set_mode(srv, "on")
    SL._rt(srv)["isolate"] = {"feel", "nudge", "reach"}
    calls = _spy(monkeypatch)
    for k in range(200):
        SL.cycle(srv, now=T0 + 100 + 11 * k)
    sends = [x for x in _lines(srv) if x.get("kind") == "nudge" and x["eid"] == COUT and x.get("moves")]
    assert calls["effects"].count("say") == len(sends) > 0
    assert len(_said(r)) == sum(1 for x in sends if x["rc"] == SL.NUDGE_MOVED)


def test_a_cycle_sees_the_pause_through_its_context(monkeypatch):
    srv = _felt()
    calls = _spy(monkeypatch)
    real = API.context

    def paused(srv, *a, **kw):
        out = real(srv, *a, **kw)
        out["nerve_paused"] = True
        return out
    monkeypatch.setattr(API, "context", paused)
    assert SL.cycle(srv, now=T0 + 100) is None                    # paused: nothing ran
    assert not calls["effects"]


# ---- the routes ----------------------------------------------------------------------------

def _jwt_env(**kw):
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


@pytest.fixture
def route():
    from membrane import server as NS
    from tests.test_server import _CFG, _StubRunner, _store
    srv = _felt()
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
    TB.place(srv, BALL)
    with _jwt_env():
        code, body = call("/membrane/self/context")
        assert code == 200 and body["self"]["eid"] == SL._eid() and len(body["current"]) == DIM
        code, body = call(f"/membrane/self/context?only=spots&reach=1&spot={BALL}")
        assert code == 200 and body["reach"][BALL] == DIM and len(body["spots"][BALL]) == DIM
        assert "things" not in body


def test_post_effects_runs_them_in_the_seat(route):
    srv, call = route
    pos = list(np.ones(DIM) / np.sqrt(DIM))
    with _jwt_env():
        code, body = call("/membrane/self/effects", {"effects": [{"kind": "position", "position": pos},
                                                                 {"kind": "nudge", "eid": BALL, "now": T0}]})
        assert code == 200 and body["results"][0] == {"ok": True}
        assert body["results"][1]["rc"] == SL.NUDGE_MOVED
        assert list(srv.entity_store.docs[SL._eid()].position) == pytest.approx(pos)
        assert call("/membrane/self/effects", {"effects": "no"})[0] == 400


def test_a_persons_token_gets_neither(route):
    from membrane import jwtauth as J
    srv, call = route
    with _jwt_env(MEMBRANE_JWT_SECRET=SECRET):
        assert call("/membrane/self/context")[0] == 401
        alice = J.mint(SECRET, "alice")
        assert call("/membrane/self/context", tok=alice)[0] == 403
        assert call("/membrane/self/effects", {"effects": []}, tok=alice)[0] == 403
        assert TB.peek("toy:ball")["side"] == "self"
