"""AN ANSWER COMES BACK (2026-10-25, the operator: decisions are reacted to
the way a fired watch is -- automatically, the model never spends a watch on
its own card). The field already knows who asked: the entity_add call that
made the card was captured as an observation event, and that event carries
its conversation (#429, `conv`: 'sid:<serve session>'). When the operator
answers the card, the seat finds that birth conversation in the field and
delivers the decision there on the path a finished job takes (a continuation
turn in a chat; a pass on its own entity for a loop). And every goal or card
the answered card `blocks` gets a pulse once all of its blockers are decided.
No property is written at creation, and no watch is planted."""
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402
from membrane import decision_map as DM  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def _wait(cond, s=5.0):
    t = time.time()
    while time.time() - t < s:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class _Ladder:
    """Every round answers with plain text naming the last user line."""
    def __init__(self):
        self.seen = []

    def __call__(self, body):
        msgs = body.get("messages") or []
        last = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
        self.seen.append(last)
        return {"choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}]}


def _seat():
    d = Path(tempfile.mkdtemp())
    est = EntityStore(path=str(d / "e.json"), shared=False)
    led = UserLedger(str(d / "f.ledger.jsonl"))
    fld = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    lad = _Ladder()
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), lad,
                      lambda *a: "", system="", model="m", tools=None)
    r._turn_watchdog = lambda *a, **k: None
    srv = types.SimpleNamespace(entity_store=est, efield=fld, user_ledger=led,
                                events_pool=WA.WatchEvents(), _loop_runner=r)
    r.srv = srv
    return srv, r, lad


def _ask(srv, name, conv, tool="mcp__membrane__entity_add", blocks=None, cid="c1"):
    """The model's entity_add, the way the proxy captures it (#429: on the
    turn's thread, so the event carries the conversation)."""
    args = {"class": "Decision", "name": name, "summary": f"about {name}",
            "properties": {"status": "open", "var": {"type": "option", "options": ["a", "b"]},
                           "pick": "a", **({"blocks": blocks} if blocks else {})}}
    est = srv.entity_store
    eid = est.add(cls="Decision", name=name, summary=args["summary"])["eid"]
    assert est.edit(eid, properties=args["properties"])["ok"]
    ev = ST._obs(tool, json.dumps(args), f"added {eid}", cid, "", None)
    srv.efield.write_events([ev], session="s:1:1", conv=conv)
    return eid


def test_the_birth_conversation_is_read_from_the_field():
    srv, _, _ = _seat()
    other = _ask(srv, "Which linter", "sid:ses_other", cid="c0")
    eid = _ask(srv, "Which test runner", "sid:ses_asker")
    card = srv.entity_store.docs[eid]
    assert DM.birth_conversation(srv.efield, card) == "sid:ses_asker"
    assert DM.birth_conversation(srv.efield, srv.entity_store.docs[other]) == "sid:ses_other"


def test_the_local_family_name_counts_too():
    srv, _, _ = _seat()
    args = {"action": "add", "class": "Decision", "name": "Which port"}
    eid = srv.entity_store.add(cls="Decision", name="Which port", summary="x")["eid"]
    srv.efield.write_events([ST._obs("entity", json.dumps(args), "ok", "c9", "", None)],
                            session="s", conv="sid:ses_local")
    assert DM.birth_conversation(srv.efield, srv.entity_store.docs[eid]) == "sid:ses_local"


def test_no_property_is_written_at_creation_and_no_watch_is_planted():
    srv, r, _ = _seat()
    sid = r.sessions.create()["id"]
    eid = _ask(srv, "Which test runner", f"sid:{sid}")
    est = srv.entity_store
    before = set(est.docs[eid].properties)
    res = DM.answer(est, srv.user_ledger, eid, "b")
    assert res["ok"], res
    DM.answered(srv, eid, runner=r)
    after = set(est.docs[eid].properties)
    assert after - before <= {"value", "status", "set_by", "set_at"}, after - before
    p = est.docs[eid].properties
    assert not p.get("watch_fields") and not p.get("watch_ops") and not p.get("watch_script")
    assert not any(eid in k for k in (getattr(est, "tracks", None) or {}))


def test_answering_delivers_to_the_birth_conversation():
    srv, r, lad = _seat()
    asker, bystander = r.sessions.create()["id"], r.sessions.create()["id"]
    eid = _ask(srv, "Which test runner", f"sid:{asker}")
    assert DM.answer(srv.entity_store, srv.user_ledger, eid, "b")["ok"]
    n0 = len(lad.seen)
    out = DM.answered(srv, eid, runner=r)
    assert out["delivered"] == "chat", out
    assert _wait(lambda: any("decided: Which test runner = b" in s for s in lad.seen[n0:]))
    line = next(s for s in lad.seen[n0:] if "decided:" in s)
    assert eid in line and "continue" in line
    rows = [p for row in r.messages.list(asker) for p in row.get("parts") or []
            if p.get("type") == "tool" and p.get("tool") == "decision"]
    assert rows, "the answer shows in the asker's chat as a tool box"
    assert not [p for row in r.messages.list(bystander) for p in row.get("parts") or []
                if p.get("tool") == "decision"]


def test_a_loop_asker_gets_a_pass_on_its_entity():
    srv, r, _ = _seat()
    r._agents["ses_loop"] = "watch: membrane::c:backup"
    eid = _ask(srv, "Which backup target", "sid:ses_loop")
    assert DM.answer(srv.entity_store, srv.user_ledger, eid, "a")["ok"]
    out = DM.answered(srv, eid, runner=r)
    assert out["delivered"] == "loop", out
    evs = srv.events_pool.take()
    [ev] = [e for e in evs if e.get("kind") == "decision"]
    assert ev["entity"] == "membrane::c:backup"
    assert "decided: Which backup target = a" in WA.loop_task_text(ev)


def test_a_card_born_nowhere_known_delivers_nothing():
    srv, r, _ = _seat()
    eid = _ask(srv, "Which shell", "head:abc123")       # a client chat, no serve session
    gone = _ask(srv, "Which editor", "sid:ses_gone", cid="c2")   # a session the seat no longer has
    for e in (eid, gone):
        assert DM.answer(srv.entity_store, srv.user_ledger, e, "a")["ok"]
    assert DM.answered(srv, eid, runner=r)["delivered"] == ""
    assert DM.answered(srv, gone, runner=r)["delivered"] == "no session"


def test_a_blocked_goal_is_pulsed_when_its_last_blocker_is_decided():
    srv, r, _ = _seat()
    est = srv.entity_store
    goal = est.add(cls="Goal", name="ship the new suite", summary="x")["eid"]
    one = _ask(srv, "Which test runner", "sid:ses_a", blocks=[goal], cid="c1")
    two = _ask(srv, "Which coverage tool", "sid:ses_a", blocks=[goal], cid="c2")
    srv.events_pool.take()
    assert DM.answer(est, srv.user_ledger, one, "a")["ok"]
    out = DM.answered(srv, one, runner=r)
    assert out["pulsed"] == [], out                    # the second card still holds it
    assert not [e for e in srv.events_pool.take() if e.get("entity") == goal]
    assert DM.answer(est, srv.user_ledger, two, "b")["ok"]
    out = DM.answered(srv, two, runner=r)
    assert out["pulsed"] == [goal], out
    assert [e for e in srv.events_pool.take()
            if e.get("kind") == "intent-planted" and e.get("entity") == goal]


def test_the_answer_route_runs_the_follow_up():
    from unittest import mock
    from tests.test_jwt_auth import _jwt_env
    from tests.test_offbox_auth import _proxy
    seen = []
    with tempfile.TemporaryDirectory() as d, \
            mock.patch.object(DM, "answered", lambda srv, eid, runner=None: seen.append(eid) or {}):
        env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(d, "seat-token"), MEMBRANE_DATA_DIR=d)
        env.start()
        backend, srv = _proxy(offbox=False)
        try:
            srv.entity_store = est = EntityStore(path=None, shared=False)
            srv.user_ledger = UserLedger(os.path.join(d, "f.ledger.jsonl"))
            eid = est.add(cls="Decision", name="Which runner", summary="x")["eid"]
            est.edit(eid, properties={"status": "open", "var": {"type": "bool"}})
            import urllib.request
            req = urllib.request.Request(
                f"http://127.0.0.1:{srv.server_address[1]}/membrane/decision/answer",
                data=json.dumps({"card": eid, "value": True}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200
            assert _wait(lambda: seen == [eid])
        finally:
            srv.shutdown()
            backend.shutdown()
            env.stop()
