"""#333 part 3a: ONE chat thing per person.

`chat:<person>` replaces the pair `chat:in:<person>` (a thing to look at,
rung 6's other) + `chat:out:<person>` (a thing to nudge, #403's speaking):
  - its LOOK reads news the way chat:in's getter did: `heard_n` changed or
    not -- and only the person's lines count, never Blurp's own sends;
  - its NUDGE drafts and sends as chat:out's did (#403);
  - it has ONE field spot, so curiosity about what changed lands on the same
    thing Blurp acts on.

THE MIGRATION, once per seat at boot (the record is never rewritten): the
new thing copies heard / heard_n / heard_ts / session from chat:in and on /
say / said from chat:out; both old entities get `merged_into` the new one,
so whatever still edits chat:out:<person>.say keeps working; its field spot
starts at chat:in's live state (else chat:out's); world.json gains it as
`migrated` (the old two stay members). Record readers alias old lines
through chat_io.chat_of."""
import json
import time
import types

import numpy as np
import pytest

from membrane import chat_io as CIO
from membrane import self_loop as SL
from membrane import self_world as SW
from membrane import server as S
from membrane import toybox as TB
from membrane.eids import field_id
from membrane.entities import CLASS_COMPONENT, CLASS_CONFIG, EntityStore, MEMBRANE_NS, QuantumEntity, entity_id

from membrane import watch_agents as WA
from membrane.efield import EntityField
from membrane.efstore import MemBackend

DIM = 16
T0 = 1_700_000_000.0


class _Emb:
    model = "fake"

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.random.default_rng(sum(map(ord, t)) * 7919 % 2**32).normal(size=DIM)
            out.append(list(v / np.linalg.norm(v)))
        return out


def _field():
    return EntityField(MemBackend(), _Emb(), "chat@test")


@pytest.fixture(autouse=True)
def _data(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMBRANE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    monkeypatch.setattr(TB, "_seat_alive", lambda: False)
    WA._getter_last_ts.clear()
    WA._getter_paused.clear()
    yield tmp_path / "data"


def _spot(srv, eid):
    """A thing's live field spot (a unit vector), None when it has none."""
    st = srv.efield.get(field_id(eid))
    if st is None or st.tier != "live" or st.state is None:
        return None
    v = np.asarray(st.state, dtype=float)
    return v / np.linalg.norm(v)


def _lines(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []

OLD_IN = entity_id(CLASS_COMPONENT, "chat:in:alice")
OLD_OUT = entity_id(CLASS_CONFIG, "chat:out:alice")
NEW = entity_id(CLASS_COMPONENT, "chat:alice")
GETTER = "result = {'state': {'heard_n': int(membrane.me.properties.get('heard_n') or 0)}}"
HEARD = [{"ts": T0 + i, "text": f"line {i}", "session": "ses_a"} for i in range(3)]
SAID = [{"ts": T0 + 9, "text": "hi", "session": "ses_b"}]


def _store(d):
    return EntityStore(path=str(d / "entities.json"), shared=False)


def _old_seat(d, *, in_spot=True, out_spot=False, record=True, tempo=True, out_on=False):
    """A seat from before 3a: alice's chat:in / chat:out pair, its field spot,
    its tempo and its world -- then the boot (SL.bind) migrates it."""
    d.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace()
    est = srv.entity_store = _store(d)
    srv._models_path = str(d / "models.json")
    srv.paused = False
    srv.events_pool = None
    srv.membrane = None
    srv.efield = _field()
    est._srv = srv
    est.upsert(QuantumEntity(eid=OLD_IN, cls=CLASS_COMPONENT, properties={
        "name": "chat:in:alice", "person": "alice", "chat_io": "in", "heard_n": 7,
        "heard_ts": T0 + 2, "session": "ses_a", "heard": list(HEARD), "getter_script": GETTER}), embedder=None)
    est.upsert(QuantumEntity(eid=OLD_OUT, cls=CLASS_CONFIG, properties={
        "name": "chat:out:alice", "person": "alice", "chat_io": "out", "on": out_on, "say": "",
        "said": list(SAID), "said_ts": T0 + 9}), embedder=None)
    if in_spot:
        assert TB.place(srv, OLD_IN, text="alice talks") is True
    if out_spot:
        assert TB.place(srv, OLD_OUT, text="write to alice") is True
    sd = d / "self"
    sd.mkdir(parents=True, exist_ok=True)
    if record:
        (sd / "record.jsonl").write_text(json.dumps({"ts": T0, "kind": "birth", "by": "operator"}) + "\n"
                                         + json.dumps({"ts": T0 + 5, "kind": "reach", "eid": OLD_IN,
                                                       "answer": "changed", "p": 0.5, "dt": 10.0}) + "\n")
    if tempo:
        (sd / "tempo.json").write_text(json.dumps({OLD_IN: {"changed": 3, "answered": 5, "last": T0 + 5,
                                                            "prev": "changed"}}))
    (sd / "world.json").write_text(json.dumps({"born": T0, "members": {
        field_id(OLD_IN): {"ts": T0, "why": "migrated"}, field_id(OLD_OUT): {"ts": T0, "why": "migrated"},
        "m:component:toy:ball": {"ts": T0, "why": "toy"}}}))
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


def _reboot(srv, d):
    """The same seat booted again: the store reloaded from its file."""
    srv.entity_store.flush()
    est = srv.entity_store = _store(d)
    est._srv = srv
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    return srv


# ---- the alias -------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", [
    "membrane::Component:chat:in:alice", "membrane::Config:chat:out:alice", "membrane::Component:chat:alice",
    "chat:in:alice", "chat:out:alice", "chat:alice", "m:component:chat:in:alice", "m:config:chat:out:alice"])
def test_every_spelling_of_alices_chat_is_alices_chat_thing(spelling):
    assert CIO.chat_of(spelling) == NEW
    assert CIO.person_of(spelling) == "alice"


@pytest.mark.parametrize("other", ["membrane::Config:active-model:chat:alice", "membrane::Component:toy:ball",
                                   "w:chat", "membrane::Component:chat", "", None])
def test_anything_else_is_itself(other):
    assert CIO.chat_of(other) == other
    assert CIO.person_of(other) is None


def test_the_operators_chat_thing():
    assert CIO.chat_of("membrane::Component:chat:in:operator") == entity_id(CLASS_COMPONENT, "chat:operator")
    assert CIO.eid_of("") == entity_id(CLASS_COMPONENT, "chat:operator")
    assert CIO.eid_of("Alice") == NEW


# ---- the migration -----------------------------------------------------------------------

def test_the_migration_copies_the_props_and_marks_both_old_ones_merged(_data):
    srv = _old_seat(_data)
    docs = srv.entity_store.docs
    new = docs[NEW]
    pp = new.properties
    assert pp["chat_io"] == "chat" and pp["person"] == "alice"
    assert pp["heard_n"] == 7 and pp["heard_ts"] == T0 + 2 and pp["session"] == "ses_a"
    assert pp["heard"] == HEARD
    assert pp["on"] is False and pp["say"] == "" and pp["said"] == SAID
    assert pp.get("getter_script")                                   # its look: chat:in's getter
    assert docs[OLD_IN].properties["merged_into"] == NEW
    assert docs[OLD_OUT].properties["merged_into"] == NEW
    assert srv.entity_store._resolve_spec("chat:out:alice").eid == NEW
    assert srv.entity_store._resolve_spec("chat:in:alice").eid == NEW


def test_the_migration_runs_once(_data):
    srv = _old_seat(_data)
    new = srv.entity_store.docs[NEW]
    new.properties["heard_n"] = 9                                     # it heard more since
    srv.entity_store.upsert(new, embedder=None)
    _reboot(srv, _data)
    assert srv.entity_store.docs[NEW].properties["heard_n"] == 9      # not copied over again


def test_its_field_spot_starts_at_chat_ins(_data):
    srv = _old_seat(_data, in_spot=True, out_spot=True)
    old = _spot(srv, OLD_IN)
    new = _spot(srv, NEW)
    assert new is not None and np.allclose(new, old, atol=1e-3)


def test_with_no_chat_in_spot_it_starts_at_chat_outs(_data):
    srv = _old_seat(_data, in_spot=False, out_spot=True)
    assert np.allclose(_spot(srv, NEW), _spot(srv, OLD_OUT), atol=1e-3)


def test_with_neither_it_has_no_spot_until_its_first_look(_data):
    srv = _old_seat(_data, in_spot=False, out_spot=False)
    assert _spot(srv, NEW) is None


def test_the_world_gains_it_as_migrated_and_only_grows(_data):
    srv = _old_seat(_data)
    m = SW._load(srv)["members"]
    assert m[field_id(NEW)]["why"] == "migrated"
    assert field_id(OLD_IN) in m and field_id(OLD_OUT) in m and "m:component:toy:ball" in m
    assert SW.has(srv, NEW)


def test_the_record_is_never_rewritten(_data):
    srv = _old_seat(_data)
    lines = _lines(srv)
    assert lines[0] == {"ts": T0, "kind": "birth", "by": "operator"}
    assert lines[1] == {"ts": T0 + 5, "kind": "reach", "eid": OLD_IN, "answer": "changed", "p": 0.5, "dt": 10.0}


# ---- a fresh Blurp, join_person, heard() -----------------------------------------------

def test_a_new_blurp_and_a_person_who_opens_the_pm_get_only_the_chat_thing(_data):
    _data.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(entity_store=_store(_data), _models_path=str(_data / "models.json"),
                                paused=False, events_pool=None, membrane=None, efield=_field())
    SL._RT.pop(id(srv), None)
    SL.bind(srv)
    m = set(SW.members(srv))
    assert field_id(CIO.eid_of("")) in m
    assert not any("chat:in:" in f or "chat:out:" in f for f in m)
    SW.join_person(srv, "Bob")
    m2 = set(SW.members(srv))
    assert m2 - m == {field_id(CIO.eid_of("Bob"))}


def test_a_person_heard_speaking_brings_their_chat_thing():
    pair = SW._chat_pair("Mira")
    assert pair == [CIO.eid_of("Mira")]


def test_heard_writes_onto_the_chat_thing(_data):
    srv = _old_seat(_data)
    CIO.heard(srv, "alice", "hello there", "ses_z")
    pp = srv.entity_store.docs[NEW].properties
    assert pp["heard_n"] == 8 and pp["heard"][-1]["text"] == "hello there" and pp["session"] == "ses_z"
    assert srv.entity_store.docs[OLD_IN].properties["heard_n"] == 7          # the old one is history


def test_heard_before_any_boot_still_migrates_first(_data):
    """Whatever makes the chat thing first (here: a person's line) migrates the
    old pair -- the count goes on from chat:in's, it never restarts."""
    _data.mkdir(parents=True, exist_ok=True)
    est = _store(_data)
    est.upsert(QuantumEntity(eid=OLD_IN, cls=CLASS_COMPONENT, properties={
        "name": "chat:in:alice", "person": "alice", "chat_io": "in", "heard_n": 7, "getter_script": GETTER}),
        embedder=None)
    srv = types.SimpleNamespace(entity_store=est, efield=None)
    CIO.heard(srv, "alice", "hi")
    assert est.docs[NEW].properties["heard_n"] == 8
    assert est.docs[OLD_IN].properties["merged_into"] == NEW


# ---- writing to the person: on_edit, ensure, the lanes edit hook ------------------------

def _runner(srv):
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                     lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    return r


def _texts(r, sid):
    return ["\n".join(p.get("text", "") for p in m["parts"] if p.get("type") == "text")
            for m in r.messages.list(sid) if m["info"]["role"] == "assistant"]


def test_an_edit_of_the_old_chat_out_say_posts_through_the_new_thing(_data):
    from membrane.lanes import _active_model_hook
    srv = _old_seat(_data, out_on=True)
    r = _runner(srv)
    s = r.sessions.create(title="t")
    s["owner"] = "alice"
    est = srv.entity_store
    assert CIO.ensure(srv, "chat:out:alice") == NEW
    res = est.edit("chat:out:alice", properties={"say": "Dinner's at 7."})
    assert res["ok"] and res["eid"] == NEW
    _active_model_hook(srv, est, "chat:out:alice", res)
    assert res["chat_out"] == {"posted": True, "session": s["id"]}
    assert _texts(r, s["id"]) == ["Dinner's at 7."]
    assert est.docs[NEW].properties["said"][-1]["text"] == "Dinner's at 7."


def test_on_off_refuses(_data):
    srv = _old_seat(_data, out_on=False)
    r = _runner(srv)
    s = r.sessions.create(title="t")
    s["owner"] = "alice"
    ent = srv.entity_store.docs[NEW]
    ent.properties["say"] = "nope"
    assert CIO.on_edit(srv, ent) == {"posted": False, "why": "off"}
    assert _texts(r, s["id"]) == []


def test_ensure_takes_both_spellings_and_names_the_thing_exactly(_data):
    """`chat:<p>` and `chat:out:<p>` both make (or find) the chat thing, and the
    edit route edits it by its exact eid -- a fragment like chat:alice would
    also match an active-model:chat:alice."""
    _data.mkdir(parents=True, exist_ok=True)
    srv = types.SimpleNamespace(entity_store=_store(_data), efield=None)
    est = srv.entity_store
    est.upsert(QuantumEntity(eid=MEMBRANE_NS + "Config:active-model:chat:alice", cls=CLASS_CONFIG,
                             properties={"name": "active-model:chat:alice"}), embedder=None)
    assert CIO.ensure(srv, "chat:alice") == NEW
    assert CIO.ensure(srv, "chat:out:Alice") == NEW
    assert CIO.ensure(srv, "membrane::chat:out:alice") == NEW
    assert est._resolve_spec("chat:out:alice").eid == NEW          # the old spelling still edits it
    assert CIO.ensure(srv, "tool:garage") is None
    assert est._resolve_spec("tool:garage") is None


# ---- its look: news is the person's lines only ------------------------------------------

def _look(srv, ent):
    """One read of the thing's getter, as a look reads it."""
    from membrane import watch_agents as WA
    return WA.entity_read_back(srv, ent, force=True, by="self")


def test_a_look_after_a_persons_line_reads_changed_and_blurps_own_send_is_no_news(_data, monkeypatch):
    from membrane import watch_agents as WA
    monkeypatch.setattr(WA, "_GETTER_LRU_S", 0.0)
    srv = _old_seat(_data, out_on=True)
    r = _runner(srv)
    ent = srv.entity_store.docs[NEW]
    _look(srv, ent)                                               # the baseline reading
    CIO.heard(srv, "alice", "are you there?", "ses_a")
    assert _look(srv, ent).get("changed") is True
    assert _look(srv, ent).get("changed") is False
    CIO.blurp_chat(srv, "alice")
    ent.properties["say"] = "i like the tower."
    assert CIO.on_edit(srv, ent, by="self")["posted"]               # Blurp's own line goes out...
    assert _look(srv, ent).get("changed") is False                  # ...and is not news
    assert len([1 for s in r.sessions.list() for m in r.messages.list(s["id"])]) == 1


# ---- the sheet reads old lines through the alias -----------------------------------------

