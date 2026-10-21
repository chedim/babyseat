"""#337: Blurp's world -- a scope inside the field.

Blurp lives in a world of its own inside its seat's field: a grow-only
membership list (self/world.json, canonical field ids). At birth it is Blurp
itself and the chat thing of its person (chat:<person>; chat:in / chat:out
until #333 part 3a); a seat whose Blurp already has a
record is migrated (its chats and the toys it has). A toy joins when it is
provisioned. Blurp HEARS THE ROOM (the operator, 2026-10-21): every discourse
event the field applies after the world's birth -- the person's messages and
the model's prose, in any chat -- adds the entities it mentions (why "heard");
one from Blurp's private chat (session blurp:<person>) joins as "chat"; what
the adults DO (tool-call observations) joins as "seen". The model's unsaid
reasoning adds nothing, and the room's past (events before birth) is not
heard or seen. Every read Blurp makes is filtered through the world.

Blurp's private chat (the PM with Blurp): one model-free session per person,
marked `blurp: true`; a message there never runs a model turn and the voice
does not answer, the field hears it under the blurp:<person> tag; Blurp's own
lines go there instead of the person's newest chat."""
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import self_world as SW  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane import toybox as TB  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.eids import field_id  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

DIM = 16
COUNTER = "n = int((state.get('data') or {}).get('n', 0)) + 1\nresult = {'state': {'n': n}}"
SELF = "m:self"
CHAT = "m:component:chat:operator"          # #333 part 3a: the chat thing (chat:in + chat:out until then)


class _World:
    def __init__(self, cur):
        self.cur = cur
        self.event_listeners = []

    def current(self):
        return np.array(self.cur, dtype=float)

    def write_events(self, evs, session=""):
        return []


def _unit(seed):
    v = np.random.default_rng(seed).normal(size=DIM)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _seat(names=(), record=None, toys=False, d=None):
    d = Path(d or tempfile.mkdtemp())
    srv = types.SimpleNamespace()
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _World(_unit(1))
    est._srv = srv
    for name in names:
        e = QuantumEntity(eid=MEMBRANE_NS + "Component:" + name, cls="Component",
                          properties={"name": name, "getter_script": COUNTER})
        est.docs[e.eid] = e
    if record:
        p = SL.record_path(srv)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in record))
    if toys:
        TB.add(srv, "fast")
    SL.bind(srv)
    return srv


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _doc(srv):
    return json.loads((SL._dir(srv) / "world.json").read_text())


# ---- the world: birth, migration, grow-only ------------------------------------------

def test_a_fresh_world_is_itself_and_its_persons_chat():
    srv = _seat(names=("thing",))
    assert SW.members(srv) == {SELF, CHAT}
    doc = _doc(srv)
    assert {m["why"] for m in doc["members"].values()} == {"birth"}
    assert doc["born"] > 0


def test_store_eids_and_field_ids_compare_in_one_space():
    srv = _seat()
    assert SW.has(srv, MEMBRANE_NS + "self")
    assert SW.has(srv, SELF)
    assert not SW.has(srv, MEMBRANE_NS + "Component:thing")
    SW.join(srv, [MEMBRANE_NS + "Component:thing"], "chat")
    assert SW.has(srv, "m:component:thing")
    assert _doc(srv)["members"]["m:component:thing"]["why"] == "chat"


def test_a_blurp_with_a_record_is_migrated_with_its_chats_and_toys():
    srv = _seat(names=("sensor",), record=[{"ts": 1.0, "kind": "cycle", "n": 1}], toys=True)
    got = SW.members(srv)
    assert SELF in got and CHAT in got
    assert field_id(MEMBRANE_NS + "Component:toy:fast") in got        # the toys it has stay
    assert "m:component:sensor" not in got                              # the coding memory does not
    assert _doc(srv)["members"][field_id(MEMBRANE_NS + "Component:toy:fast")]["why"] == "migrated"


def test_the_world_only_grows_and_survives_a_restart():
    d = tempfile.mkdtemp()
    srv = _seat(d=d)
    SW.join(srv, ["w:ball"], "heard")
    SW.join(srv, ["w:ball"], "chat")                    # a member stays itself: first reason kept
    assert _doc(srv)["members"]["w:ball"]["why"] == "heard"
    srv2 = _seat(d=d)                                     # the same dir: a reboot
    assert {SELF, CHAT, "w:ball"} <= SW.members(srv2)


def test_a_toy_joins_when_it_is_provisioned():
    srv = _seat()
    out = TB.add(srv, "slow")
    assert out["ok"], out
    fid = field_id(out["eids"][0])
    assert SW.has(srv, fid)
    assert _doc(srv)["members"][fid]["why"] == "toy"


# ---- every read is filtered -----------------------------------------------------------

def test_an_entity_outside_the_world_is_never_looked_at():
    from _self_cap import cap
    srv = _seat(names=("inside", "outside"))
    SW.join(srv, [MEMBRANE_NS + "Component:inside"], "toy")
    cap(srv, 2)
    for i in range(40):
        SL.cycle(srv, now=1000.0 + i * SL.PACE_S)
    looked = {r.get("eid") for r in _record(srv) if r.get("kind") == "reach"}
    assert MEMBRANE_NS + "Component:inside" in looked
    assert MEMBRANE_NS + "Component:outside" not in looked
    assert MEMBRANE_NS + "Component:outside" not in SL._targets(srv)


def test_a_direct_reach_outside_the_world_reads_nothing():
    srv = _seat(names=("outside",))
    SL._ctx.cycle = {"srv": srv, "act": "reach", "now": 1000.0}
    try:
        SL._reach(srv, MEMBRANE_NS + "Component:outside")
        read = SL._ctx.cycle["read"]
    finally:
        SL._ctx.cycle = None
    assert read["fired"] is False and "world" in read["reason"]
    assert "script_data" not in srv.entity_store.docs[MEMBRANE_NS + "Component:outside"].properties


def test_the_ball_and_the_mirror_outside_the_world_are_not_blurps():
    srv = _seat()
    for kind in ("ball", "mirror"):
        TB.add(srv, kind, locked_ok=True)
    ball = MEMBRANE_NS + "Component:toy:ball"
    assert SL._in_world(srv, ball) and SL._mirror_toy(srv) is not None   # #333: the ball is nudged, never rolled
    rt = SL._rt(srv)
    rt["world"]["members"] = {k: v for k, v in rt["world"]["members"].items() if "toy:" not in k}
    assert not SL._in_world(srv, ball)                                   # ...so a nudge at it is refused
    assert SL._mirror_toy(srv) is None


# ---- Blurp hears the room -------------------------------------------------------------

def _blurp_line(srv, text, person):
    """Blurp's own line to a person's chat:out, as the chat sees it (#333:
    _push_chat went with meet; chat:out's own nudge will say it)."""
    ent = CIO.entity(srv, person)
    ent.properties["say"] = text
    CIO.on_edit(srv, ent, by="self")


def _ev(session, stream="discourse", role="user", mentions=("w:ball",), ts=None):
    return {"stream": stream, "role": role, "session": session, "ts": time.time() if ts is None else ts,
            "mentions": [[m, 0, m.split(":", 1)[1]] for m in mentions]}


def test_anything_said_in_any_chat_after_birth_joins_as_heard():
    srv = _seat()
    SW.on_event(srv, _ev("s:1:0", role="user", mentions=("w:ball",)))
    SW.on_event(srv, _ev("s:1:0", role="model", mentions=("w:tower",)))
    assert {"w:ball", "w:tower"} <= SW.members(srv)
    assert _doc(srv)["members"]["w:ball"]["why"] == "heard"


def test_a_person_heard_speaking_brings_their_chat_into_the_world():
    """The operator, 2026-10-21: hearing someone speak, in any chat, adds that
    person's chat thing -- even a line that mentions nothing."""
    srv = _seat()
    pair = {SW._fid(e) for e in SW._chat_pair("Mira")}
    assert not pair & SW.members(srv)
    ev = _ev("s:1:0", role="user", mentions=()) | {"speaker": ["person:mira", "Mira"]}
    SW.on_event(srv, ev)
    assert pair <= SW.members(srv)
    assert {_doc(srv)["members"][f]["why"] for f in pair} == {"heard"}


def test_a_persons_line_in_blurps_chat_brings_their_chat_as_chat():
    srv = _seat()
    pair = {SW._fid(e) for e in SW._chat_pair("Mira")}
    SW.on_event(srv, _ev("blurp:mira", role="user", mentions=()) | {"speaker": ["person:mira", "Mira"]})
    assert {_doc(srv)["members"][f]["why"] for f in pair} == {"chat"}


def test_blurps_own_line_is_its_own_role_on_the_field():
    """The operator, 2026-10-21: Blurp's lines are role `self` on the field, never
    the model's prose -- its words stay its own."""
    from membrane import streams as ST
    evs = ST.self_line("ball: changed", ts=5.0)
    assert [(e.stream, e.role, e.text) for e in evs] == [("discourse", "self", "ball: changed")]


def test_blurps_own_line_in_its_chat_joins_as_chat():
    srv = _seat()
    SW.on_event(srv, _ev("blurp:operator", role="self", mentions=("w:kite",)))
    assert _doc(srv)["members"]["w:kite"]["why"] == "chat"


def test_what_is_said_in_blurps_chat_joins_as_chat():
    srv = _seat()
    SW.on_event(srv, _ev("blurp:operator", mentions=("w:kite",)))
    assert _doc(srv)["members"]["w:kite"]["why"] == "chat"


def test_what_the_adults_do_joins_as_seen():
    """The adults' ACTIONS count too (the operator, 2026-10-21): a tool call the
    field applies after birth, in any chat, adds what it touches."""
    srv = _seat()
    SW.on_event(srv, _ev("s:1:0", stream="observation", role="tool", mentions=("f:a.py", "c:pytest")))
    assert {"f:a.py", "c:pytest"} <= SW.members(srv)
    assert _doc(srv)["members"]["f:a.py"]["why"] == "seen"


def test_the_rooms_past_and_unsaid_thoughts_add_nothing():
    srv = _seat()
    born = _doc(srv)["born"]
    SW.on_event(srv, _ev("s:1:0", mentions=("w:old",), ts=born - 60))
    SW.on_event(srv, _ev("s:1:0", stream="observation", role="tool", mentions=("f:old.py",), ts=born - 60))
    SW.on_event(srv, _ev("s:1:0", role="reasoning", mentions=("w:thought",)))
    SW.on_event(srv, _ev("s:1:0", stream="discourse", role="user", mentions=("w:untimed",), ts=None) | {"ts": None})
    got = SW.members(srv)
    assert not {"w:old", "f:old.py", "w:thought", "w:untimed"} & got


def test_the_field_hands_every_applied_event_to_its_event_listeners():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "test@host")
    seen = []
    f.event_listeners.append(lambda key, ev: seen.append((key, ev.get("session"), ev.get("mentions"))))
    keys = f.write_events([ST.Event(stream="discourse", role="user", text="the red ball rolls under the tower",
                                    ts=time.time())], session="blurp:operator")
    f.process()
    assert [k for k, _, _ in seen] == keys
    assert seen[0][1] == "blurp:operator"
    assert any(m[0] == "w:ball" for m in seen[0][2])


def test_bind_registers_the_world_on_the_field():
    srv = _seat()
    assert len(srv.efield.event_listeners) == 1
    SL.bind(srv)
    assert len(srv.efield.event_listeners) == 1            # once
    srv.efield.event_listeners[0]("k", _ev("s:x", mentions=("w:moon",)))
    assert SW.has(srv, "w:moon")


# ---- Blurp's private chat (the PM with Blurp) -------------------------------------------

class _Cap:
    def __init__(self):
        self.turns = []

    def on_turn(self, query, prose="", thought="", ts=None, session=None):
        self.turns.append({"query": query, "prose": prose, "session": session})

    def on_self(self, text, session=None, ts=None):
        self.turns.append({"self": text, "session": session})


def _runner_seat(complete=None):
    srv = _seat()
    srv.capturer = _Cap()
    calls = []
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                     complete or (lambda b: calls.append(b) or {"choices": [{"message": {"content": "ok"}}]}),
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    return srv, r, calls


def _texts(r, sid, role="assistant"):
    return ["\n".join(p.get("text", "") for p in m["parts"] if p.get("type") == "text")
            for m in r.messages.list(sid) if m["info"]["role"] == role]


class TestBlurpsChat(unittest.TestCase):
    def test_one_private_chat_per_person_marked_as_blurps(self):
        srv, r, _ = _runner_seat()
        a = CIO.blurp_chat(srv, "Ann")
        self.assertEqual(CIO.blurp_chat(srv, "Ann"), a)
        b = CIO.blurp_chat(srv, "")
        self.assertNotEqual(a, b)
        row = r.sessions.get(a)
        self.assertTrue(row["blurp"])
        self.assertEqual(row["owner"], "Ann")
        self.assertIn("PM with", row["title"])
        self.assertTrue(SW.has(srv, "m:component:chat:ann"))           # #333 part 3a: its chat thing

    def test_no_create_finds_nothing(self):
        srv, r, _ = _runner_seat()
        self.assertEqual(CIO.blurp_chat(srv, "Ann", create=False), "")

    def test_others_lines_never_land_in_blurps_chat(self):
        srv, r, _ = _runner_seat()
        coding = r.sessions.create(title="code")
        coding["owner"] = "Ann"
        coding["time"]["updated"] = 1000
        pm = CIO.blurp_chat(srv, "Ann")
        r.sessions.get(pm)["time"]["updated"] = 2000            # newer, still never the model's chat
        self.assertEqual(CIO.latest_chat(srv, "Ann"), coding["id"])

    def test_blurps_own_line_goes_to_its_private_chat_and_the_field_hears_it(self):
        srv, r, _ = _runner_seat()
        coding = r.sessions.create(title="code")
        coding["owner"] = "Ann"
        _blurp_line(srv, "self is here", "Ann")
        pm = CIO.blurp_chat(srv, "Ann", create=False)
        self.assertTrue(pm)
        self.assertEqual(_texts(r, pm), [CIO.SELF_MARK + " self is here"])
        self.assertEqual(_texts(r, coding["id"]), [])
        self.assertEqual(srv.capturer.turns[-1]["session"], "blurp:ann")
        self.assertIn("self is here", srv.capturer.turns[-1]["self"])     # Blurp's own role, never the model's

    def test_a_message_in_blurps_chat_runs_no_model_and_no_voice(self):
        srv, r, calls = _runner_seat()
        pm = CIO.blurp_chat(srv, "")
        from unittest import mock
        with mock.patch("membrane.voice.on_turn_end") as end, mock.patch("membrane.voice.on_turn_start") as start:
            out = r.run(pm, "look, a ball")
        self.assertEqual(calls, [])
        end.assert_not_called()
        start.assert_not_called()
        self.assertEqual(_texts(r, pm, "user"), ["look, a ball"])
        self.assertEqual(_texts(r, pm), [])
        self.assertEqual(srv.capturer.turns[-1], {"query": "look, a ball", "prose": "", "session": "blurp:operator"})
        self.assertEqual(out["parts"], [])

    def test_who_chatted_last_is_moved_by_the_persons_words_not_blurps(self):
        srv, r, _ = _runner_seat()
        bob = r.sessions.create(title="code")
        bob["owner"] = "Bob"
        _blurp_line(srv, "self is here", "Ann")            # Blurp opens Ann's PM and speaks there
        self.assertEqual(CIO.recent_person(srv), "Bob")
        r.run(CIO.blurp_chat(srv, "Ann"), "hi Blurp")        # Ann answers in it
        self.assertEqual(CIO.recent_person(srv), "Ann")

    def test_a_message_in_another_chat_still_stamps_the_chat_thing(self):
        """The room (the operator, 2026-10-21): Blurp hears every chat -- the
        chat thing (chat:in until #333 part 3a) keeps its old rule; the PM is
        only where Blurp speaks."""
        srv, r, _ = _runner_seat()
        h = _Route(srv, r)
        try:
            coding = r.sessions.create(title="code")
            code, _ = h.post(f"/session/{coding['id']}/message", {"parts": [{"type": "text", "text": "hi"}]})
            ent = CIO.entity(srv, "", create=False)
            self.assertEqual(int(ent.properties["heard_n"]), 1)
            pm = CIO.blurp_chat(srv, "")
            h.post(f"/session/{pm}/message", {"parts": [{"type": "text", "text": "hello you"}]})
            self.assertEqual(int(ent.properties["heard_n"]), 2)
        finally:
            h.close()

    def test_the_route_gets_or_creates_the_callers_private_chat(self):
        srv, r, calls = _runner_seat()
        h = _Route(srv, r)
        try:
            code, body = h.post("/membrane/self/chat", {})
            self.assertEqual(code, 200)
            self.assertTrue(body["blurp"])
            code2, body2 = h.post("/membrane/self/chat", {})
            self.assertEqual(body2["id"], body["id"])
            code, _ = h.post(f"/session/{body['id']}/message", {"parts": [{"type": "text", "text": "a kite"}]})
            self.assertEqual(code, 200)
            self.assertEqual(calls, [])
            self.assertEqual(srv.capturer.turns[-1]["session"], "blurp:operator")
        finally:
            h.close()


class _Route:
    def __init__(self, srv, runner):
        from tests.test_server import _CFG
        handler = S.make_handler(hub=runner.hub, sessions=runner.sessions, messages=runner.messages,
                                 config=_CFG, runner=runner)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, None

    def close(self):
        self.httpd.shutdown()
