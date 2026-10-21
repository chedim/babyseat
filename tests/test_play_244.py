"""#244 (part of #161): play with self -- the operator, a person on the page
and the seat's model act on toybox toys, every change tagged by who did it.

  - `by` gains <person> (the operator / the page's person) and `model` (the
    seat's model) beside self / world, which keep their meaning;
  - the actions: tower knock <k> (topples k..top) / rebuild <k> (lifts k),
    die roll, drift / burst nudge; body stays self-only (a poke is refused);
  - the surfaces: `membrane toybox poke <toy> <action> [arg]` (the operator),
    the toybox object's poke method (the model), and POST
    /membrane/toybox/poke (the page's person, behind the JWT gate);
  - self reads others' changes as changes it didn't cause: its reach record
    keeps who changed the thing since its last look (`by`, ground truth --
    never in the reading);
  - every toy (and, on its first reach, anything self looks at) gets a spot
    in the field, and status() counts the looks that still land off it;
  - the page draws a look at an unplaced thing as a ghost at a stable spot
    near the amoeba, never as a field position.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import night as N  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from tests._self_cap import cap  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
DIM = 16
SECRET = "s" * 40


def _unit(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


class _World:
    def __init__(self, cur):
        self.cur = cur

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


class _Emb:
    model = "fake"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            v = np.random.default_rng(sum(map(ord, t)) * 7919 % 2**32).normal(size=DIM)
            out.append(list(v / np.linalg.norm(v)))
        return out


def _field(emb=None):
    """A real EntityField on a memory backend; its current() is self's world."""
    fld = EntityField(MemBackend(), emb if emb is not None else _Emb(), "play@test")
    fld.current = lambda now=None: np.array(_unit(1), dtype=float)
    return fld


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _seat(fld=None):
    d = Path(os.environ["MEMBRANE_DATA_DIR"])
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = fld if fld is not None else _World(_unit(1))
    est._srv = srv
    SL.bind(srv)
    TB.bind(srv)
    return srv


def _eid(name):
    return MEMBRANE_NS + "Component:" + name


def _log(name):
    return TB.state(name)["log"]


def _standing(name):
    return [b["standing"] for b in TB.state(name)["blocks"]]


# ---- the actions, each tagged ------------------------------------------------------

def test_knock_topples_k_and_everything_above_it_tagged_with_the_person():
    TB.create("tower", "toy:tower", {"n": 5}, now=0.0)
    out = TB.poke("toy:tower", "knock", "3", by="alice", now=10.0)
    assert out["ok"], out
    assert _standing("toy:tower") == [True, True, False, False, False]
    bl = TB.state("toy:tower")["blocks"]
    assert all(b["by"] == "alice" and b["at"] == 10.0 for b in bl[2:])
    last = _log("toy:tower")[-1]
    assert last["by"] == "alice" and last["what"] == "topple" and last["blocks"] == [3, 4, 5]
    assert TB.state("toy:tower")["counts"]["alice"] == 1


def test_rebuild_lifts_only_block_k():
    TB.create("tower", "toy:tower", {"n": 5}, now=0.0)
    TB.poke("toy:tower", "knock", 2, by="alice", now=1.0)
    out = TB.poke("toy:tower", "rebuild", 4, by="model", now=2.0)
    assert out["ok"], out
    assert _standing("toy:tower") == [True, False, False, True, False]
    last = _log("toy:tower")[-1]
    assert last == {"ts": 2.0, "by": "model", "what": "lift", "block": 4}


def test_tower_pokes_that_make_no_change_are_refused():
    TB.create("tower", "toy:tower", {"n": 3}, now=0.0)
    assert not TB.poke("toy:tower", "rebuild", 2, by="alice")["ok"]     # it is standing
    TB.poke("toy:tower", "knock", 2, by="alice")
    assert not TB.poke("toy:tower", "knock", 3, by="alice")["ok"]       # already down
    assert not TB.poke("toy:tower", "knock", 9, by="alice")["ok"]       # no such block
    assert not TB.poke("toy:tower", "knock", None, by="alice")["ok"]    # which one?
    assert not TB.poke("toy:tower", "spin", 1, by="alice")["ok"]        # no such action
    assert len(_log("toy:tower")) == 1


def test_a_block_can_be_named_directly():
    TB.create("tower", "toy:tower", {"n": 4}, now=0.0)
    assert TB.poke("toy:tower:b2", "knock", by="alice")["ok"]
    assert _standing("toy:tower") == [True, False, False, False]


def test_die_roll_changes_the_face_tagged():
    TB.create("die", "toy:die", {"seed": 4}, now=0.0)
    before = TB.state("toy:die")["value"]
    out = TB.poke("toy:die", "roll", by="bob", now=3.0)
    assert out["ok"] and out["reading"] == {"face": TB.state("toy:die")["value"]}
    assert TB.state("toy:die")["value"] != before
    last = _log("toy:die")[-1]
    assert last["by"] == "bob" and last["act"] == "roll" and last["value"] == TB.state("toy:die")["value"]


def test_drift_nudge_steps_within_bounds_and_burst_nudge_changes():
    TB.create("drift", "toy:drift", {"seed": 1, "lo": 0, "hi": 20, "step": 5}, now=0.0)
    v0 = TB.state("toy:drift")["value"]
    assert TB.poke("toy:drift", "nudge", "up", by="alice")["ok"]
    assert TB.state("toy:drift")["value"] == v0 + 5
    assert TB.poke("toy:drift", "nudge", "down", by="alice")["ok"]
    assert TB.state("toy:drift")["value"] == v0
    for _ in range(5):
        TB.poke("toy:drift", "nudge", "up", by="alice")
    assert 0 <= TB.state("toy:drift")["value"] <= 20                  # a wall: it bounces
    assert TB.poke("toy:drift", "nudge", None, by="alice")["ok"]       # no direction: a coin
    assert not TB.poke("toy:drift", "nudge", "sideways", by="alice")["ok"]
    assert {e["by"] for e in _log("toy:drift")} == {"alice"}
    TB.create("burst", "toy:burst", {"seed": 1}, now=0.0)
    b0 = TB.state("toy:burst")["value"]
    assert TB.poke("toy:burst", "nudge", by="model")["ok"]
    assert TB.state("toy:burst")["value"] != b0
    assert _log("toy:burst")[-1]["by"] == "model"


def test_body_is_self_only_and_the_clocks_take_no_pokes():
    TB.create("body", "toy:body", {}, now=0.0)
    v = TB.state("toy:body")["value"]
    out = TB.poke("toy:body", "nudge", by="alice")
    assert not out["ok"] and "self" in out["error"]
    assert TB.state("toy:body")["value"] == v and _log("toy:body") == []
    for kind in ("fast", "slow"):
        TB.create(kind, "toy:" + kind, {}, now=0.0)
        assert not TB.poke("toy:" + kind, "nudge", by="alice")["ok"]


def test_the_old_tags_keep_their_meaning_and_a_poke_cannot_claim_them():
    TB.create("die", "toy:die", {"seed": 2}, now=0.0)
    for who in ("self", "world", "", None):
        out = TB.poke("toy:die", "roll", by=who)
        assert out["ok"]
        assert out["by"] not in ("self", "world", "")
    assert TB.poke("toy:die", "roll", by="Alice Smith")["by"] == "Alice Smith"
    assert TB.poke("toy:die", "roll", by="model")["by"] == "model"
    assert TB.who("") == "operator" and TB.who("self") == "person:self"
    TB.tick("toy:die", now=1e9)                                          # its own clock: still the world's
    assert _log("toy:die")[-1]["by"] == "world"
    assert TB.read("toy:die", by="alice") == {"face": TB.state("toy:die")["value"]}   # reads stay minimal


def test_pokes_persist_across_a_restart():
    TB.create("tower", "toy:tower", {"n": 3}, now=0.0)
    TB.poke("toy:tower", "knock", 2, by="alice", now=1.0)
    TB._forget()
    assert _standing("toy:tower") == [True, False, False]
    assert TB.state("toy:tower")["counts"] == {"self": 0, "world": 0, "alice": 1}
    row = [r for r in TB.list_toys() if r["name"] == "toy:tower"][0]
    assert row["counts"]["alice"] == 1 and "alice=1" in TB._fmt([row])


# ---- the surfaces -------------------------------------------------------------------

def test_cli_poke_is_tagged_with_the_operator(capsys):
    TB.create("tower", "toy:tower", {"n": 3}, now=0.0)
    assert TB.cli_main(["poke", "toy:tower", "knock", "2"]) == 0
    assert _log("toy:tower")[-1]["by"] == "operator"
    assert "toy:tower" in capsys.readouterr().out
    assert TB.cli_main(["poke", "toy:tower", "rebuild", "2", "--as", "alice"]) == 0
    assert _log("toy:tower")[-1]["by"] == "alice"
    assert TB.cli_main(["poke", "toy:tower", "knock", "7"]) != 0


def test_cli_poke_works_under_a_live_seat(monkeypatch):
    """A poke touches the toy's own files (locked), never the entity store."""
    monkeypatch.setattr(TB, "_seat_alive", lambda: True)
    TB.create("die", "toy:die", {}, now=0.0)
    assert TB.cli_main(["poke", "toy:die", "roll"]) == 0


def _call(srv, method, args=None):
    return N.method_call(srv, srv.entity_store, None, None, "toybox", method, args or {})


def test_the_toybox_object_pokes_as_the_model():
    srv = _seat()
    TB.add(srv, "tower", params={"n": 4})
    out = _call(srv, "poke", {"toy": "toy:tower", "action": "knock 3"})
    assert out["rc"] == 0, out
    assert _standing("toy:tower") == [True, True, False, False]
    assert _log("toy:tower")[-1]["by"] == "model"
    out = _call(srv, "poke", {"toy": "toy:tower:b3", "action": "rebuild"})
    assert out["rc"] == 0, out
    TB.add(srv, "body")
    out = _call(srv, "poke", {"toy": "toy:body", "action": "nudge"})
    assert out["rc"] != 0 and "self" in out["head"]


def test_an_older_seat_gets_the_poke_method_on_boot():
    srv = _seat()
    est = srv.entity_store
    for eid, d in list(est.docs.items()):
        if (d.properties or {}).get("method_name") == "poke":
            est.docs.pop(eid)
    TB.bind(srv)
    assert any((d.properties or {}).get("method_name") == "poke" for d in est.docs.values())


def _jwt_env(**kw):
    base = {k: v for k, v in os.environ.items() if not k.startswith("MEMBRANE_JWT_")}
    base.update(kw)
    return mock.patch.dict(os.environ, base, clear=True)


class _Route:
    def __init__(self, srv):
        from membrane import server as NS
        from tests.test_server import _CFG, _StubRunner, _store
        runner = _StubRunner()
        runner.srv = srv
        handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                                  config=_CFG, runner=runner)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def post(self, path, body, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, None

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def route():
    srv = _seat()
    TB.add(srv, "tower", params={"n": 5})
    TB.add(srv, "die")
    r = _Route(srv)
    yield r
    r.close()


def test_the_route_tags_the_pages_person(route, _data):
    with _jwt_env(MEMBRANE_STANDALONE="1", MEMBRANE_DATA_DIR=str(_data)):
        code, body = route.post("/ui/serve/membrane/toybox/poke", {"toy": "toy:tower", "action": "knock", "arg": 4},
                                headers={"X-Membrane-Speaker": "alice"})
        assert code == 200 and body["ok"], body
        assert _log("toy:tower")[-1]["by"] == "alice"
        assert _standing("toy:tower") == [True, True, True, False, False]
        code, body = route.post("/ui/serve/membrane/toybox/poke", {"toy": "toy:die", "action": "roll",
                                                                   "person": "mallory"})
        assert code == 200 and _log("toy:die")[-1]["by"] == "guest"     # the page's line, never the body's


def test_the_route_is_toybox_actions_only(route):
    with _jwt_env():
        code, body = route.post("/membrane/toybox/poke", {"toy": "toy:tower", "action": "remove"})
        assert code == 400 and not body["ok"]
        code, body = route.post("/membrane/toybox/poke", {"toy": "toy:nope", "action": "roll"})
        assert code == 400
        code, body = route.post("/membrane/toybox/poke", ["not", "an", "object"])
        assert code == 400


def test_the_route_is_behind_the_jwt_gate_and_tags_the_token(route):
    from membrane import jwtauth as J
    with _jwt_env(MEMBRANE_JWT_SECRET=SECRET):
        code, _ = route.post("/membrane/toybox/poke", {"toy": "toy:die", "action": "roll"})
        assert code == 401
        assert _log("toy:die") == []
        code, body = route.post("/membrane/toybox/poke", {"toy": "toy:die", "action": "roll", "person": "mallory"},
                                headers={"Authorization": "Bearer " + J.mint(SECRET, "alice"),
                                         "X-Membrane-Speaker": "mallory"})
        assert code == 200 and body["ok"], body
        assert _log("toy:die")[-1]["by"] == "alice"


def test_the_ha_relay_lets_the_poke_through_with_the_ha_user():
    relay = (ROOT / "custom_components" / "membrane" / "relay.py").read_text()
    allowed = relay.split("ALLOWED_EXACT = (", 1)[1].split(")", 1)[0]
    assert '"membrane/toybox/poke"' in allowed
    person = relay.split("PERSON_PATHS = (", 1)[1].split(")", 1)[0]
    assert '"membrane/toybox/poke"' in person


# ---- self reads it as a change it didn't cause -------------------------------------

def _climb(srv, t):
    cap(srv, 1)
    for _ in range(SL.RUNG1_CYCLES * 2):
        SL.cycle(srv, now=t)
        t += SL.PACE_S
    assert SL.status(srv)["rung"] == 1
    cap(srv, 2)
    return t


def _reaches(srv, eid):
    return [r for r in SL._read(srv) if r.get("kind") == "reach" and r.get("eid") == eid]


def test_a_persons_knock_reads_to_self_as_a_change_it_did_not_cause(monkeypatch):
    srv = _seat()
    TB.add(srv, "tower", params={"n": 5})
    t = _climb(srv, 1000.0)
    whole = _eid("toy:tower")
    monkeypatch.setattr(SL, "_targets", lambda srv: [whole])
    SL.cycle(srv, now=t)
    t += SL.PACE_S
    first = _reaches(srv, whole)[-1]
    assert first["by"] == []                                       # nothing changed it yet
    TB.poke("toy:tower", "knock", 3, by="alice")
    SL.cycle(srv, now=t)
    t += SL.PACE_S
    look = _reaches(srv, whole)[-1]
    assert look["answer"] == "changed"
    assert look["by"] == ["alice"] and "self" not in look["by"]    # the ground truth: alice did it
    SL.cycle(srv, now=t)
    assert _reaches(srv, whole)[-1]["by"] == [] and _reaches(srv, whole)[-1]["answer"] == "unchanged"


def test_selfs_own_nudge_is_tagged_self_on_its_record(monkeypatch):
    srv = _seat()
    TB.add(srv, "tower", params={"n": 5})
    t = _climb(srv, 1000.0)
    b4 = _eid("toy:tower:b4")
    monkeypatch.setattr(SL, "_targets", lambda srv: [b4])
    SL.cycle(srv, now=t)
    assert _reaches(srv, b4)[-1]["by"] == []                         # #333: a look topples nothing
    SL._nudge_once(srv, 0, t + 1, b4, None)                          # #333: self topples it by a nudge
    SL.cycle(srv, now=t + SL.PACE_S)
    assert _reaches(srv, b4)[-1]["by"] == ["self"]
    TB.poke("toy:tower", "rebuild", 4, by="model")
    SL._nudge_once(srv, 0, t + SL.PACE_S + 1, b4, None)
    SL.cycle(srv, now=t + 2 * SL.PACE_S)
    assert _reaches(srv, b4)[-1]["by"] == ["model", "self"]          # the model lifted it, self toppled it again


# ---- a spot in the field ---------------------------------------------------------------

def _live(fld, eid):
    st = fld.get(field_id(eid))
    return st is not None and st.tier == "live" and st.state is not None


def test_a_new_toy_gets_a_field_spot_from_its_summary():
    emb = _Emb()
    fld = _field(emb)
    srv = _seat(fld)
    TB.add(srv, "die")
    assert _live(fld, _eid("toy:die"))
    TB.add(srv, "tower", params={"n": 3})
    assert all(_live(fld, _eid(n)) for n in ("toy:tower", "toy:tower:b1", "toy:tower:b3"))
    a = np.asarray(fld.get(field_id(_eid("toy:die"))).state, float)
    b = np.asarray(fld.get(field_id(_eid("toy:tower"))).state, float)
    assert float(a @ b) < 0.999                                    # its own spot, not a shared one


def test_placing_is_idempotent_and_quiet_without_an_embedder():
    fld = _field()
    srv = _seat(fld)
    TB.add(srv, "die")
    st = fld.get(field_id(_eid("toy:die")))
    before = list(st.state)
    assert TB.place(srv, _eid("toy:die")) is False                  # already there: nothing moves
    assert list(fld.get(field_id(_eid("toy:die"))).state) == before
    fld2 = _field()
    fld2.emb = None
    srv2 = _seat(fld2)
    assert TB.add(srv2, "drift", name="toy:d2")["ok"]               # no embedder: the toy still comes
    assert TB.place(srv2, _eid("toy:d2")) is None
    srv3 = _seat()                                                  # a field without place(): quiet
    assert TB.add(srv3, "drift", name="toy:d3")["ok"]


def test_migrate_gives_the_replaced_toys_their_spots(_data):
    fld = _field()
    srv = _seat(fld)
    est = srv.entity_store
    e = QuantumEntity(eid=_eid("w:fast"), cls="Component",
                      properties={"name": "w:fast", "getter_script": "result={'state':{}}"})
    est.docs[e.eid] = e
    TB.migrate(srv, TB.migration_plan(est))
    assert _live(fld, _eid("w:fast"))


def _thing(srv, name, summary=""):
    props = {"name": name, "getter_script": "result = {'state': {'v': 1}}"}
    if summary:
        props["summary"] = summary
    e = QuantumEntity(eid=name, cls="Component", properties=props)
    srv.entity_store.docs[e.eid] = e
    return e.eid


def test_selfs_first_reach_places_what_it_looks_at(monkeypatch):
    emb = _Emb()
    fld = _field(emb)
    srv = _seat(fld)
    eye = _thing(srv, "field:vitals", "the field's vital signs")
    t = _climb(srv, 1000.0)
    assert not _live(fld, eye)
    monkeypatch.setattr(SL, "_targets", lambda srv: [eye])
    SL.cycle(srv, now=t)
    assert _live(fld, eye)
    calls = emb.calls
    SL.cycle(srv, now=t + SL.PACE_S)
    assert emb.calls == calls                                      # placed once, never again
    assert _reaches(srv, eye)[-1]["fid"] == field_id(eye)


def test_the_off_field_count():
    fld = _field()
    fld.emb = None                                                 # nothing can be placed
    srv = _seat(fld)
    for i, eid in enumerate(("i", "w:rung", "i")):
        SL._append(srv, {"ts": 100.0 + i, "kind": "reach", "n": i, "eid": eid, "answer": "changed", "p": 0.5})
    off = SL.status(srv)["off_field"]
    assert off["looks"] == 3 and off["off"] == 3 and set(off["things"]) == {"i", "w:rung"}
    fld.emb = _Emb()
    assert TB.place(srv, "w:rung") is True
    SL._rt(srv).pop("off_field", None)
    off = SL.status(srv)["off_field"]
    assert off["off"] == 2 and off["things"] == ["i"]


# ---- the page: the play buttons and the ghost ----------------------------------------

NODE = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _js(module, expr):
    js = ('const M=require(%r); process.stdout.write(JSON.stringify(%s))' % (str(UI / module), expr))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
def test_each_toy_gets_its_play_buttons():
    acts = lambda t: [(a["action"], a.get("arg")) for a in _js("panel.js", "M.toyActions(%s)" % json.dumps(t))]
    assert acts({"eid": _eid("toy:tower:b3"), "kind": "tower", "toy": "toy:tower", "block": 3}) == \
        [("knock", 3), ("rebuild", 3)]
    assert acts({"eid": _eid("toy:tower"), "kind": "tower", "toy": "toy:tower"}) == []
    assert acts({"eid": _eid("toy:die"), "kind": "die", "toy": "toy:die"}) == [("roll", None)]
    assert acts({"eid": _eid("toy:drift"), "kind": "drift", "toy": "toy:drift"}) == [("nudge", None)]
    assert acts({"eid": _eid("toy:burst"), "kind": "burst", "toy": "toy:burst"}) == [("nudge", None)]
    assert acts({"eid": _eid("toy:body"), "kind": "body", "toy": "toy:body"}) == []
    assert acts({"eid": "w:fast", "kind": "script"}) == []


@NODE
def test_the_card_carries_the_buttons_escaped():
    st = {"self": {"ladder": [{"rung": 1, "name": "x", "state": "current"}], "toys": [
        {"eid": _eid("toy:tower:b2"), "kind": "tower", "toy": "toy:tower", "block": 2, "getter": True,
         "reads": 0, "answered": 0, "health": "unread"},
        {"eid": _eid('toy:"x'), "kind": "die", "toy": 'toy:"x', "getter": True, "reads": 0, "answered": 0,
         "health": "unread"}]}}
    html = _js("panel.js", "M.selfCardHtml(%s)" % json.dumps(st))
    assert 'data-poke-toy="toy:tower" data-poke-action="knock" data-poke-arg="2"' in html
    assert 'data-poke-action="rebuild"' in html and 'data-poke-action="roll"' in html
    assert 'data-poke-toy="toy:&quot;x"' in html                    # escaped in the attribute


def test_the_panel_posts_the_one_write_and_home_wires_the_buttons():
    panel = (UI / "panel.js").read_text()
    home = (UI / "home.js").read_text()
    assert '"/membrane/toybox/poke"' in panel and "_toyPoke" in panel
    assert "data-poke-toy" in home and "_toyPoke" in home


@NODE
def test_the_ghost_direction_is_stable_unit_and_per_eid():
    d1 = _js("field3d.js", "M.ghostDir('i')")
    d2 = _js("field3d.js", "M.ghostDir('i')")
    d3 = _js("field3d.js", "M.ghostDir('field:vitals')")
    assert d1 == d2 and d1 != d3
    for d in (d1, d3):
        assert abs(sum(x * x for x in d) - 1) < 1e-9
    many = _js("field3d.js", "['a','b','c','d','e','f','g','h'].map(M.ghostDir)")
    assert len({tuple(round(x, 6) for x in d) for d in many}) == 8


@NODE
def test_the_ghost_decision():
    a = [10, 20, 30]
    real = _js("field3d.js", "M.lookTarget([1,2,3], 'i', %s)" % json.dumps(a))
    assert real == {"p": [1, 2, 3], "ghost": False}
    g = _js("field3d.js", "M.lookTarget(null, 'i', %s)" % json.dumps(a))
    assert g["ghost"] is True
    r = sum((g["p"][i] - a[i]) ** 2 for i in range(3)) ** 0.5
    assert abs(r - _js("field3d.js", "M.GHOST_R")) < 1e-6           # a fixed short radius from the amoeba
    assert g["p"] == _js("field3d.js", "M.lookTarget(null, 'i', %s).p" % json.dumps(a))
    assert _js("field3d.js", "M.lookTarget(null, 'i', null)") == {"p": None, "ghost": False}   # no amoeba: nothing
    assert _js("field3d.js", "M.lookLabel('the eye', true)") == "the eye (unplaced)"
    assert _js("field3d.js", "M.lookLabel('the eye', false)") == "the eye"


def test_the_ghost_is_drawn_dashed_and_never_on_the_trail():
    src = (UI / "field3d.js").read_text()
    body = src.split("_stepBrain(now) {", 1)[1].split("\n  }\n", 1)[0]
    assert "lookTarget(" in body and "ghost" in body
    assert "LineDashedMaterial" in src and "(not on the field)" not in src


@NODE
def test_a_ghost_looks_label_goes_through_the_layout():
    """The ghost's chip is laid out like every label (#237's layoutLabels):
    clamped into the visible field, stepped aside from self's tag."""
    assert _js("field3d.js", "M.lookChip('field:vitals', 'unchanged', true)") == "👁 field:vitals (unplaced) · unchanged"
    assert _js("field3d.js", "M.lookChip('w:burst', 'changed', false)") == "👁 w:burst ✓ changed"
    # a ghost spot right where self's tag sits: the tag keeps its place, the ghost's chip steps aside
    out = _js("field3d.js", "M.layoutLabels(["
              "{key:'tag', ax:500, ay:300, w:120, h:18, prio:9, dx:14, dy:-9},"
              "{key:'look', ax:505, ay:302, w:160, h:18, prio:8, dx:0, dy:-9}],"
              " {x0:0, y0:0, x1:1000, y1:700})")
    t, lk = out["tag"], out["look"]
    assert not lk["hidden"]
    assert lk["y"] + 18 <= t["y"] or t["y"] + 18 <= lk["y"]          # no overlap
    # a ghost behind a panel: the chip waits at the field's edge with an arrow
    out = _js("field3d.js", "M.layoutLabels([{key:'look', ax:50, ay:300, w:160, h:18, prio:8}], {x0:300, y0:0, x1:1000, y1:700})")
    assert out["look"]["out"] and out["look"]["arrow"] and out["look"]["x"] >= 300
    src = (UI / "field3d.js").read_text()
    body = src.split("_layoutSelfLabels(now) {", 1)[1].split("\n  }\n", 1)[0]
    assert "lookTarget(cur.p, cur.eid, a.pos)" in body and "lookChip(" in body and 'add("look"' in body
    assert "(not on the field)" not in src


@NODE
def test_the_feed_says_who_changed_a_toy():
    line = _js("panel.js", "M.selfLine(%s)" % json.dumps(
        {"kind": "reach", "eid": _eid("toy:tower"), "p": 0.3, "answer": "changed", "by": ["alice"]}))
    assert line.endswith("· by alice")
    plain = _js("panel.js", "M.selfLine(%s)" % json.dumps(
        {"kind": "reach", "eid": "w:fast", "p": 0.3, "answer": "changed", "by": []}))
    assert " by " not in plain
