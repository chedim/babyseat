"""#729: the watch loops TRIAGE ONLY. A watch event on an entity with an
`escalate_when` note runs one read-only triage pass on the seat's local idle
model; it answers `ignored: <reason>` (nothing in the chat) or `ESCALATE
<line>` (one message into the latest chat, through #700's route, which starts
a main-model turn and queues behind a running one). An event whose entity has
no note goes straight to the chat, with no loop run. Goal pulses, wakes and
model calls, restart notices, job results and the goal machinery's own
events (an answered loop card, a fulfilled goal) keep their loops (the scope
card)."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as S  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore, QuantumEntity  # noqa: E402

PUMP = "membrane::Component:bilge-pump"


class _Sessions:
    def __init__(self, rows):
        self.rows = rows

    def list(self):
        return list(self.rows)


class _Runner:
    """The serve runner as #700's route sees it: the chats, and run()."""
    def __init__(self):
        self.sessions = _Sessions([{"id": "ses_old", "time": {"updated": 1}},
                                   {"id": "ses_op", "time": {"updated": 5}},
                                   {"id": "ses_loop", "time": {"updated": 9}}])
        self._agents = {"ses_loop": "watch: x"}


class _Srv:
    def __init__(self, note: str | None = "the pump runs dry", answer: str = "ignored: still wet"):
        self.entity_store = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        self.entity_store._srv = self
        props = {"name": "bilge pump", "state": "on"}
        if note is not None:
            props["escalate_when"] = note
        self.entity_store.docs[PUMP] = QuantumEntity(eid=PUMP, cls="Component", properties=props)
        self.sent = []
        self.entity_store._decision_chat = lambda sid, text: self.sent.append((sid, text))
        self._loop_runner = _Runner()
        self.loops = WA.LoopRunner(max_loops=3)
        self.loops.titles = []
        self.turns = []
        self.answer = answer
        self.create_loop_session = lambda agent, title=None: "ses_triage"
        self.run_loop_turn = lambda sid, text, agent: self.turns.append((agent, text)) or {"text": self.answer}


def _ev(kind="near", **kw):
    return {"kind": kind, "entity": PUMP, "rel": "c:dry", "distance": 0.2, "event_id": "e1", **kw}


def test_escalate_puts_one_message_in_the_latest_chat():
    srv = _Srv(answer="The pump is dry.\nESCALATE the bilge pump ran dry")
    assert WA._LoopDrainer(srv)._handle(_ev()) == "escalate"
    [(agent, task)] = srv.turns
    assert agent.startswith("triage")
    assert "the pump runs dry" in task                 # the planter's rule rides the task
    [(sid, text)] = srv.sent
    assert sid == "ses_op"                             # the latest chat, never a loop's
    assert PUMP in text and "the bilge pump ran dry" in text


def test_ignored_puts_nothing_in_the_chat():
    srv = _Srv(answer="ignored: still wet")
    assert WA._LoopDrainer(srv)._handle(_ev()) == "ignored"
    assert len(srv.turns) == 1 and srv.sent == []
    stamped = srv.entity_store.docs[PUMP].properties.get("watch_decisions") or []
    assert stamped and stamped[-1]["verdict"] == "ignored"


def test_no_note_goes_straight_to_the_chat_with_no_loop():
    srv = _Srv(note=None)
    assert WA._LoopDrainer(srv)._handle(_ev()) == "escalate"
    assert srv.turns == []
    [(sid, text)] = srv.sent
    assert sid == "ses_op" and PUMP in text


def test_an_unreadable_answer_escalates():
    srv = _Srv(answer="hmm, maybe")
    assert WA._LoopDrainer(srv)._handle(_ev()) == "escalate"
    assert len(srv.sent) == 1


def test_the_old_working_verdicts_retire_from_triage():
    v, _ = WA.parse_triage("DONE closed it")
    assert v == "escalate"                             # not a triage verdict: the chat decides
    assert WA.parse_triage("ESCALATE x") == ("escalate", "x")
    assert WA.parse_triage("**ignored:** quiet") == ("ignored", "quiet")


def test_goal_pulses_and_wakes_keep_their_loops():
    srv = _Srv(note=None, answer="STEP moved")
    for ev in (_ev("intent-planted", text="g"), _ev("llm-call", prompt="p"),
               _ev("restart-pending", due=0), _ev("job-done", job=1),
               _ev("decision", title="t", value="a"), _ev("goal-fulfilled", title="g")):
        assert not WA.is_triage_event(ev)
    assert WA.is_triage_event(_ev("field:changed"))
    assert WA.is_triage_event(_ev("boundary"))


def test_a_triage_loop_is_refused_write_tools():
    lane = S._lane_of(WA.loop_agent_name("triage", PUMP))
    tools = S._lane_tools(lane)
    for w in ("entity_edit", "entity_add", "code_run", "method_call", "intend", "fulfill", "field_watch"):
        assert w not in tools
    assert "entity_read" in tools and "query" in tools


def test_an_escalation_rides_the_runners_queue(monkeypatch=None):
    """No hook: #700's route runs the turn through runner.run (its per-session
    queue holds it behind a running turn; nothing is cut off)."""
    import threading
    srv = _Srv(note=None)
    del srv.entity_store._decision_chat
    ran = threading.Event()
    calls = []
    srv._loop_runner.run = lambda sid, text: calls.append((sid, text)) or ran.set()
    srv._loop_runner.abort = lambda *a, **k: calls.append("abort")
    WA._LoopDrainer(srv)._handle(_ev())
    assert ran.wait(2)
    assert calls == [("ses_op", calls[0][1])]
