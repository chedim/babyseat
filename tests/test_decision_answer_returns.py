"""An answered Decision card wakes the watcher loops (2026-10-25): the answer
puts one `decision` event (the card, its title and value, what it blocks)
into the loops' existing events pool, the way a fired watch arrives, and the
drainer admits a loop pass on it."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import decision_map as DM  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.ledger import UserLedger  # noqa: E402


def _seat():
    d = Path(tempfile.mkdtemp())
    est = EntityStore(path=str(d / "e.json"), shared=False)
    est._events_pool = WA.WatchEvents()
    return est, UserLedger(str(d / "f.ledger.jsonl"))


def _card(est, name, **props):
    eid = est.add(cls="Decision", name=name, summary=f"about {name}")["eid"]
    assert est.edit(eid, properties={"status": "open", "var": {"type": "option",
                                     "options": ["pytest", "unittest"]}, **props})["ok"]
    return eid


def test_an_answer_enqueues_one_decision_event():
    est, led = _seat()
    goal = est.add(cls="Goal", name="ship the new suite", summary="x")["eid"]
    eid = _card(est, "Which test runner", blocks=[goal])
    assert DM.answer(est, led, eid, "pytest")["ok"]
    [ev] = est._events_pool.take()
    assert (ev["kind"], ev["entity"], ev["title"], ev["value"]) == \
        ("decision", eid, "Which test runner", "pytest")
    assert ev["blocks"] == [goal]
    text = WA.loop_task_text(ev)
    assert "decided: Which test runner = pytest" in text and goal in text


def test_a_refused_answer_enqueues_nothing():
    est, led = _seat()
    eid = _card(est, "Which test runner")
    assert not DM.answer(est, led, eid, "nose")["ok"]
    assert est._events_pool.take() == []


def test_the_drainer_wakes_a_loop_pass_on_it():
    est, led = _seat()
    eid = _card(est, "Which test runner")
    assert DM.answer(est, led, eid, "unittest")["ok"]
    [ev] = est._events_pool.take()

    class _Srv:
        entity_store = est
        events_pool = est._events_pool
        loops = WA.LoopRunner(max_loops=3)
    turns = []
    srv = _Srv()
    srv.create_loop_session = lambda agent, title=None: "ses_loop"
    srv.run_loop_turn = lambda sid, text, agent: turns.append((agent, text)) or {"text": "STEP noted"}
    WA._LoopDrainer(srv).handle(ev)
    [(agent, text)] = turns
    assert eid in agent and "decided: Which test runner = unittest" in text
