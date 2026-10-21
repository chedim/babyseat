"""#208: self meets the person who actually chats, not the empty operator slot.

Rung 6's act pushed to chat:out:operator and reached chat:in:operator as "the
other" on every seat -- on a seat with people that slot never answers, so self
talked to nobody for 44 h and rung 7's bar sat at 0. Self now meets whoever
chatted last (chat_io.recent_person, the owner of the newest owned chat), the
operator only on a seat with no people; when the person changes the push
target and the reach target move together; its line is visibly self's; and
rungs 6/7 read the CURRENT other's answers (a stale other's looks don't carry
them -- the passed line stays on the record, the bar re-measures and slips
until the real person earns it).
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import chat_io as CIO  # noqa: E402
from membrane import self_loop as SL  # noqa: E402
from membrane import server as S  # noqa: E402
from membrane.entities import EntityStore, MEMBRANE_NS  # noqa: E402

DIM = 16
OP_IN = MEMBRANE_NS + "Component:chat:in:operator"
ROOM_IN = MEMBRANE_NS + "Component:chat:in:roomata-estorsky"


class _World:
    def current(self):
        v = np.random.default_rng(1).normal(size=DIM)
        return v / np.linalg.norm(v)

    def write_events(self, evs, session=""):
        return []


def _seat(passed=(1, 2, 3, 4, 5), limit=7):
    d = Path(tempfile.mkdtemp())
    srv = types.SimpleNamespace(paused=False, events_pool=None, membrane=None, efield=_World())
    est = srv.entity_store = EntityStore(path=str(d / "entities.json"), shared=False)
    srv._models_path = str(d / "models.json")
    est._srv = srv
    r = S.TurnRunner(S.SSEHub(), S.SessionStore(), S.MessageStore(),
                     lambda b: {"choices": [{"message": {"content": "ok"}}]},
                     lambda n, a, c: "ok", system="", model="m")
    r.srv = srv
    srv._loop_runner = r
    SL.bind(srv)
    from tests._self_cap import world
    world(srv)                              # #337: what it is handed to look at is in its world
    SL.set_limit(srv, 1)
    for n in passed:
        SL._append(srv, {"ts": 1.0, "kind": "rung", "rung": n, "skill": 0.9})
    SL.set_limit(srv, limit)
    return srv, r


def _chat(r, owner="", updated=1000):
    from membrane import self_world as SW
    from tests._self_cap import world
    world(r.srv, *SW._chat_pair(owner))     # #337: the person who chats is in self's world
    s = r.sessions.create(title="t")
    if owner:
        s["owner"] = owner
    s["time"]["updated"] = updated
    return s["id"]


def _record(srv):
    p = SL.record_path(srv)
    return [json.loads(ln) for ln in p.read_text().splitlines() if ln.strip()] if p.exists() else []


def _pushes(srv):
    return [x for x in _record(srv) if x["kind"] == "push"]


def _assistant(r, sid):
    return [m for m in r.messages.list(sid) if m["info"]["role"] == "assistant"]


def test_self_meets_the_person_who_chats_not_the_operator_slot():
    srv, r = _seat()
    _chat(r, "roomata-estorsky", updated=2000)
    SL._meet(srv, "1", now=5000.0)
    p = _pushes(srv)[-1]
    assert p["eid"] == "chat:out:roomata-estorsky"
    assert p["to"] == ROOM_IN
    assert SL._rt(srv)["other"] == ROOM_IN                 # rung 7 predicts the same one
    assert ROOM_IN in srv.entity_store.docs                # a real reach target


def test_a_seat_with_no_people_falls_back_to_the_operator():
    srv, r = _seat()
    _chat(r, "", updated=2000)
    SL._meet(srv, "1", now=5000.0)
    p = _pushes(srv)[-1]
    assert p["eid"] == "chat:out:operator" and p["to"] == OP_IN


def test_when_the_person_changes_self_follows_push_and_reach_together():
    srv, r = _seat()
    ann = _chat(r, "Ann", updated=2000)
    SL._meet(srv, "1", now=5000.0)
    bob = _chat(r, "Bob", updated=3000)                     # Bob chats later
    SL._meet(srv, "31", now=6000.0)
    a, b = _pushes(srv)[-2:]
    assert (a["eid"], a["to"]) == ("chat:out:ann", MEMBRANE_NS + "Component:chat:in:ann")
    assert (b["eid"], b["to"]) == ("chat:out:bob", MEMBRANE_NS + "Component:chat:in:bob")
    assert SL._rt(srv)["other"] == b["to"]
    pm_ann, pm_bob = CIO.blurp_chat(srv, "Ann", create=False), CIO.blurp_chat(srv, "Bob", create=False)
    assert len(_assistant(r, pm_ann)) == 1 and len(_assistant(r, pm_bob)) == 1   # #337: each line in its person's PM
    assert _assistant(r, ann) == [] and _assistant(r, bob) == []                   # ...never their coding chat


def test_selfs_line_is_visibly_selfs():
    srv, r = _seat()
    _chat(r, "roomata-estorsky", updated=2000)
    SL._meet(srv, "1", now=5000.0)
    [m] = _assistant(r, CIO.blurp_chat(srv, "roomata-estorsky", create=False))   # #337: its PM
    assert m["info"].get("from") == "self"
    text = "".join(p.get("text", "") for p in m["parts"] if p.get("type") == "text")
    assert text.startswith("\U0001F331")                    # the sprout: self, not the seat
    assert all(p.get("from") == "self" for p in m["parts"])


def test_an_ordinary_chat_out_line_is_not_marked_as_self():
    srv, r = _seat()
    sid = _chat(r, "Ann", updated=2000)
    out = CIO.entity(srv, "Ann")
    out.properties["say"] = "The garage door is open."
    CIO.on_edit(srv, out)
    [m] = _assistant(r, sid)
    assert "from" not in m["info"]
    assert m["parts"][0]["text"] == "The garage door is open."


def test_the_panel_names_selfs_line():
    js = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "panel.js").read_text()
    assert 'info.from === "self"' in js


def _looks(eid, n, base=0.0):
    out = []
    for i in range(n):
        out.append({"ts": base + i, "kind": "reach", "eid": eid, "n": i, "steer": True,
                    "answer": "changed" if i % 2 else "unchanged", "p": 0.5,
                    "reply": True, "replied": bool(i % 2), "p_self": 1.0 if i % 2 else 0.0})   # #254: rung 7 reads self's p on reply checks
    return out


def _push(to, n, base=0.0):
    return [{"ts": base + i, "kind": "push", "n": i, "eid": "chat:out:x", "to": to} for i in range(n)]


def test_rung6_reads_only_the_current_others_answers():
    tempo = {OP_IN: {"changed": 10, "answered": 20}}
    old = _push(OP_IN, SL.RUNG6_PUSHES) + _looks(OP_IN, SL.RUNG6_HEARD)
    assert SL._rung6(old, tempo)["passed"] is True          # it passed against the old other
    now = old + _push(ROOM_IN, 1, base=100.0)               # the person changed
    out = SL._rung6(now, tempo)
    assert out["eid"] == ROOM_IN
    assert out["crit"]["pushes"] == 1 / SL.RUNG6_PUSHES     # pushes to the old slot don't count
    assert out["crit"]["heard"] == 0.0 and out["passed"] is False


def test_rung8_reads_only_the_current_others_pairs():   # rung 7 until #258
    old = _push(OP_IN, 1) + _looks(OP_IN, SL.RUNG8_LOOKS)
    assert SL._rung8(old)["passed"] is True
    now = old + _push(ROOM_IN, 1, base=100.0)
    out = SL._rung8(now)
    assert out["crit"]["looks"] == 0.0 and out["passed"] is False


def test_a_passed_rung6_stays_on_the_record_and_slips_until_the_person_earns_it():
    srv, r = _seat(passed=(1, 2, 3, 4, 5, 6), limit=6)
    for x in _push(OP_IN, SL.RUNG6_PUSHES) + _push(ROOM_IN, 1, base=100.0):
        SL._append(srv, x)
    st = SL.status(srv)
    assert st["rung"] == 6                                  # history is not rewritten
    assert st["slipping"] is True                           # the bar re-measures against the real person
    assert st["meets"]["other"] == ROOM_IN
