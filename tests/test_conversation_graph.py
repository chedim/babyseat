"""THE CONVERSATION AS A GRAPH OVER THE FIELD (operator, 2026-11-01).

Pinned here:
  - a chat turn mints both messages as `msg:` entities: the reply -follows-> the
    person's message, which -follows-> the reply before it; each -refers-to-> the
    specific entities it names, and the person's message -led-to-> every program
    root the turn ran;
  - a reply's numbered "Open decisions" become Decision cards in that chat (the same
    question is the same card; one no longer asked is dropped, quietly), each
    -asks:N-> from the reply; the person's "1. yes" answers card 1 through
    decision_map.answer (the value on the -answers-> edge), with no decision message
    sent back (the chat already says it); a bare "yes" answers the only open one,
    "yesx2" both; with several open a bare answer answers nothing;
  - a card an agent opened in the chat is asked in it: membrane posts it, numbered
    after the reply's own, and the answer finds it the same way;
  - the words membrane says are templates (speech.say);
  - "delete it": the antecedent is read from the reply before (its -refers-to->),
    the readings that can't apply drop (a method of that name, then the entity's
    state), one left is recorded as what "it" refers to, two left re-ask in the chat
    ("it": a or b?) and the model turn does not run; the answer then reads normally.
"""
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import convgraph as CG  # noqa: E402
from membrane import relations as R  # noqa: E402
from membrane import speech as SP  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import CLASS_DECISION, EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402

SID = "ses_chat"


class _Ledger:
    def __init__(self):
        self.lines = []

    def append(self, line, conv=""):
        self.lines.append(line)
        return True


class _Msgs:
    def __init__(self):
        self.rows = {}

    def list(self, sid):
        return list(self.rows.get(sid, []))

    def add(self, sid, mid, role, text):
        self.rows.setdefault(sid, []).append({"info": {"id": mid, "role": role, "sessionID": sid,
                                                      "time": {"created": len(self.rows.get(sid, []))}},
                                             "parts": [{"type": "text", "text": text}]})
        return {"id": mid, "role": role, "sessionID": sid}


class _Runner:
    def __init__(self, srv):
        self.srv, self.messages, self.posted = srv, _Msgs(), []
        self._current_turn_sid = None

    def post_line(self, sid, text):
        self.posted.append((sid, text))


def _seat():
    f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
    est = EntityStore(path=None, shared=False)
    srv = types.SimpleNamespace(efield=f, entity_store=est, membrane=None, user_ledger=_Ledger(),
                                capturer=types.SimpleNamespace(mode="on"))
    est._srv = srv
    est.sent = []
    est._decision_chat = lambda sid, text: est.sent.append((sid, text))
    runner = _Runner(srv)
    srv._loop_runner = runner
    return srv, runner


def _turn(srv, runner, n, user, reply, roots=()):
    u = runner.messages.add(SID, f"msg_u{n}", "user", user)
    runner._current_turn_sid = SID
    for r in roots:
        CG.note_root(srv, r)
    runner._current_turn_sid = None
    a = runner.messages.add(SID, f"msg_a{n}", "assistant", reply)
    CG.on_turn_end(srv, runner, SID, u, user, a, reply)
    return CG.msg_eid(u["id"]), CG.msg_eid(a["id"])


def _out(srv, eid, rel=None):
    rows = R.of(srv.efield.b, srv.efield.origin, eid)
    return [(r["rel"], r["obj"]) for r in rows
            if r["subj"] == eid and (rel is None or str(r["rel"]).startswith(rel))]


def _cards(srv, status=None):
    return [d for d in srv.entity_store.docs.values() if d.cls == CLASS_DECISION
            and (status is None or (d.properties or {}).get("status") == status)]


class TheGraph(unittest.TestCase):
    def test_a_turn_mints_both_messages_and_they_follow(self):
        srv, runner = _seat()
        u1, a1 = _turn(srv, runner, 1, "hello", "hi there")
        u2, a2 = _turn(srv, runner, 2, "and now?", "now this")
        docs = srv.entity_store.docs
        self.assertEqual(docs[u1].properties["role"], "user")
        self.assertEqual(docs[a1].properties["text"], "hi there")
        self.assertIn(("follows", u1), _out(srv, a1))
        self.assertIn(("follows", a1), _out(srv, u2))

    def test_a_message_refers_to_the_files_it_names(self):
        srv, runner = _seat()
        u, _ = _turn(srv, runner, 1, "review membrane/watch_agents.py please", "ok")
        self.assertIn(("refers-to", "f:membrane/watch_agents.py"), _out(srv, u))

    def test_the_message_led_to_the_programs_its_turn_ran(self):
        srv, runner = _seat()
        u, _ = _turn(srv, runner, 1, "run the tests", "done", roots=[MEMBRANE_NS + "node:sh:abc"])
        self.assertIn(("led-to", MEMBRANE_NS + "node:sh:abc"), _out(srv, u))


REPLY = "Here is the plan.\n\nOpen decisions:\n1. Should I merge #800?\n2. Restart the seat now?"


class DecisionsInTheChat(unittest.TestCase):
    def test_a_replys_numbered_decisions_become_cards_it_asks(self):
        srv, runner = _seat()
        _, a = _turn(srv, runner, 1, "go", REPLY)
        cards = _cards(srv, "open")
        self.assertEqual(sorted(c.properties["name"] for c in cards),
                         ["Restart the seat now?", "Should I merge #800?"])
        self.assertTrue(all(c.properties["chat"] == SID for c in cards))
        asks = dict(_out(srv, a, "asks:"))
        self.assertEqual(srv.entity_store.docs[asks["asks:1"]].properties["name"], "Should I merge #800?")

    def test_the_same_question_is_the_same_card_and_one_dropped_goes_quietly(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "go", REPLY)
        _turn(srv, runner, 2, "hmm", "Still open.\n\nOpen decisions:\n1. Should I merge #800?")
        self.assertEqual(len(_cards(srv, "open")), 1)
        self.assertEqual([c.properties["name"] for c in _cards(srv, "dropped")], ["Restart the seat now?"])
        self.assertEqual(srv.entity_store.sent, [])

    def test_a_numbered_answer_decides_its_card(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "go", REPLY)
        u, _ = _turn(srv, runner, 2, "1. yes", "merging")
        decided = {c.properties["name"]: c.properties.get("value") for c in _cards(srv, "decided")}
        self.assertEqual(decided, {"Should I merge #800?": "yes"})
        self.assertTrue(any(rel == "answers" for rel, _ in _out(srv, u)))
        self.assertEqual(srv.entity_store.sent, [])          # no "[decision] ..." sent back
        self.assertTrue(srv.user_ledger.lines)

    def test_a_bare_yes_answers_the_only_open_one(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "go", "Done.\n\nOpen decisions:\n1. Push to origin?")
        _turn(srv, runner, 2, "yes", "pushed")
        self.assertEqual([c.properties["value"] for c in _cards(srv, "decided")], ["yes"])

    def test_yesx2_answers_both(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "go", REPLY)
        _turn(srv, runner, 2, "yesx2", "ok")
        self.assertEqual(sorted(c.properties["value"] for c in _cards(srv, "decided")), ["yes", "yes"])

    def test_a_bare_answer_with_two_open_answers_nothing(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "go", REPLY)
        _turn(srv, runner, 2, "only to the chat", "ok")
        self.assertEqual(_cards(srv, "decided"), [])

    def test_an_agent_card_is_asked_in_the_chat_and_answered_from_it(self):
        srv, runner = _seat()
        srv.entity_store.upsert(QuantumEntity(eid=MEMBRANE_NS + "decision:k", cls=CLASS_DECISION, properties={
            "name": "Use port 8080?", "status": "open", "planted_by": "chat", "chat": SID,
            "var": {"type": "bool"}}))
        _turn(srv, runner, 1, "set it up", REPLY)
        self.assertEqual(len(runner.posted), 1)
        self.assertIn("3. Use port 8080?", runner.posted[0][1])
        _turn(srv, runner, 2, "3. no", "ok")
        card = srv.entity_store.docs[MEMBRANE_NS + "decision:k"]
        self.assertIs(card.properties["value"], False)


class TheSpeakerLine(unittest.TestCase):
    """A chat client's speaker line ('[user: X]') heads the person's text: it is not their words."""

    def test_the_message_is_named_by_its_words_and_keeps_its_speaker(self):
        srv, runner = _seat()
        u, _ = _turn(srv, runner, 1, "[user: Dima]\nreview membrane/watch_agents.py", "ok")
        p = srv.entity_store.docs[u].properties
        self.assertEqual(p["name"], "review membrane/watch_agents.py")
        self.assertEqual(p["speaker"], "Dima")
        self.assertIn(("refers-to", "f:membrane/watch_agents.py"), _out(srv, u))

    def test_an_answer_under_a_speaker_line_answers(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "[user: Dima]\ngo", REPLY)
        _turn(srv, runner, 2, "[user: Dima]\n1. yes", "merging")
        self.assertEqual([c.properties["value"] for c in _cards(srv, "decided")], ["yes"])

    def test_a_pronoun_under_a_speaker_line_is_resolved(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
        runner.messages.add(SID, "msg_u2", "user", "[user: Dima]\ndelete it")
        out = CG.before_turn(srv, runner, SID, {"id": "msg_u2"}, "[user: Dima]\ndelete it")
        self.assertTrue(out and out.get("reask"))


class Speech(unittest.TestCase):
    def test_the_templates(self):
        self.assertEqual(SP.say("decided", title="t", value="v"), "[decision] t = v")
        self.assertEqual(SP.say("goal-fulfilled", goal="g", receipt="r"), "goal g fulfilled: r")
        self.assertEqual(SP.say("reask", word="it", options=["a", "b"]), '"it": a or b?')
        self.assertEqual(SP.say("asks", items=[(1, "x?"), (2, "y?")]), "Open decisions:\n1. x?\n2. y?")


class TheReferent(unittest.TestCase):
    def _two_files(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
        return srv, runner

    def test_two_readings_re_ask_and_skip_the_turn(self):
        srv, runner = self._two_files()
        runner.messages.add(SID, "msg_u2", "user", "delete it")
        out = CG.before_turn(srv, runner, SID, {"id": "msg_u2"}, "delete it")
        self.assertTrue(out and out.get("reask"))
        self.assertEqual(runner.posted, [(SID, '"it": membrane/a.py or membrane/b.py?')])

    def test_a_closed_reading_drops_and_the_one_left_is_recorded(self):
        srv, runner = _seat()
        est = srv.entity_store
        for name, status in (("alpha", "closed"), ("beta", "open")):
            est.upsert(QuantumEntity(eid=MEMBRANE_NS + f"goal:{name}", cls="Goal",
                                     properties={"name": name, "status": status}))
        _, a = _turn(srv, runner, 1, "look", "x")
        for name in ("alpha", "beta"):
            R.upsert(srv.efield.b, srv.efield.origin, a, "refers-to", MEMBRANE_NS + f"goal:{name}", "chat")
        runner.messages.add(SID, "msg_u2", "user", "close it")
        out = CG.before_turn(srv, runner, SID, {"id": "msg_u2"}, "close it")
        self.assertFalse(out and out.get("reask"))
        self.assertIn(("refers-to", MEMBRANE_NS + "goal:beta"), _out(srv, CG.msg_eid("msg_u2")))

    def test_a_reading_with_the_method_wins(self):
        srv, runner = self._two_files()
        est = srv.entity_store
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "thing:svc", cls="Component", properties={"name": "svc"}))
        est.upsert(QuantumEntity(eid=MEMBRANE_NS + "method:deploy", cls="Component", properties={
            "name": "deploy", "method_name": "deploy", "method_of": [MEMBRANE_NS + "thing:svc"],
            "command": "echo deploy"}))
        _, a = _turn(srv, runner, 2, "and the service?", "svc is ready")
        R.upsert(srv.efield.b, srv.efield.origin, a, "refers-to", MEMBRANE_NS + "thing:svc", "chat")
        R.upsert(srv.efield.b, srv.efield.origin, a, "refers-to", "f:membrane/a.py", "chat")
        runner.messages.add(SID, "msg_u3", "user", "deploy it")
        out = CG.before_turn(srv, runner, SID, {"id": "msg_u3"}, "deploy it")
        self.assertFalse(out and out.get("reask"))
        self.assertIn(("refers-to", MEMBRANE_NS + "thing:svc"), _out(srv, CG.msg_eid("msg_u3")))

    def test_no_pronoun_no_resolution(self):
        srv, runner = self._two_files()
        self.assertIsNone(CG.before_turn(srv, runner, SID, {"id": "msg_u9"}, "what changed?"))


class TheRunnerWiring(unittest.TestCase):
    def test_a_real_turn_lands_in_the_graph_and_a_tie_skips_the_model(self):
        import threading
        from membrane import server as MS
        calls = []

        def ladder(body):
            raise AssertionError("streamed")

        def stream(body, on_event):
            calls.append(body)
            return {"choices": [{"message": {"content": "I changed membrane/a.py and membrane/b.py."},
                                 "finish_reason": "stop"}]}
        ladder.stream = stream
        srv, _fake = _seat()
        r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                          lambda *a: "ok", system="", model="m", tools=None)
        r.srv = srv
        srv._loop_runner = r
        sid = r.sessions.create(title="t")["id"]
        r.run(sid, "look at the code")
        for th in [t for t in threading.enumerate() if t.name == "convgraph"]:
            th.join(5)
        msgs = [d for d in srv.entity_store.docs.values() if d.cls == CG.CLASS_MESSAGE]
        self.assertEqual(sorted(d.properties["role"] for d in msgs), ["assistant", "user"])
        n = len(calls)
        out = r.run(sid, "delete it")
        self.assertEqual(out.get("reask"), '"it": membrane/a.py or membrane/b.py?')
        self.assertEqual(len(calls), n)                       # the model was not called
        last = r.messages.list(sid)[-1]
        self.assertEqual(last["info"]["role"], "assistant")
        self.assertIn('"it":', last["parts"][-1]["text"])


if __name__ == "__main__":
    unittest.main()
