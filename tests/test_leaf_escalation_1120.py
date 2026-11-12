"""#1120 hotfix (operator 2026-11-11), after the live run where qwen3:8b thought for 117 s and returned nothing:
1. a leaf worker on a local model runs with thinking off (the think dial's own `think` key in the body);
2. a leaf on the `workers` model that ends failed / stuck / empty / capped runs ONCE more on the `loops` model;
   the second run's result is the task's, both runs are in the ledger, and the report counts escalations. RED first."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import goalgraph as GG  # noqa: E402
from membrane import minds_models as MM  # noqa: E402
from membrane import rag as RAG  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from test_background_work_minds import CLAUDE, _runner, _session, _Srv  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401

A = GG.task_eid(GOAL, "a")
U8 = {"model": "qwen3:8b", "input": 10, "cache_read": 0, "cache_write": 0, "output": 0, "steps": 1,
      "estimated": True}
US = {"model": CLAUDE, "input": 50, "cache_read": 0, "cache_write": 0, "output": 9, "steps": 2,
      "estimated": False}


# ---- 1. thinking off for a local leaf ------------------------------------------------------------------------
class TestThinkingOffForLeaves(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv)

    def _body(self, agent):
        sid = _session(self.r, agent, "")
        self.r.run(sid, "go")
        return self.bodies[-1]

    def test_a_local_leaf_asks_for_no_thinking(self):
        MM.apply(self.srv, workers="qwen3:8b")
        self.assertIs(self._body("worker: " + A).get("think"), False)

    def test_the_dial_keeps_it_off_when_the_seat_says_on(self):
        self.assertIs(RAG.apply_think_dial({"think": False}, True)["think"], False)

    def test_a_chat_and_a_loop_are_left_to_the_dial(self):
        self.assertNotIn("think", self._body("build"))
        self.assertNotIn("think", self._body("watch: membrane::goal:abc"))

    def test_a_claude_leaf_gets_no_think_key(self):
        MM.apply(self.srv, workers=CLAUDE)
        self.assertNotIn("think", self._body("worker: " + A))


# ---- 2. escalation: once, on the loops model ---------------------------------------------------------------------
class TestTheEscalatedLeafRunsOnTheLoops(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv, model=CLAUDE)
        MM.apply(self.srv, loops=CLAUDE)

    def test_the_escalated_agent_runs_on_the_loops_pick(self):
        MM.apply(self.srv, workers="qwen3:8b")
        sid = _session(self.r, GG.escalated_agent(A), "")
        self.r.run(sid, "go")
        self.assertEqual(self.bodies[-1]["model"], CLAUDE)
        self.assertNotIn("think", self.bodies[-1])


def _workers(seat, answers, workers="qwen3:8b", loops=CLAUDE):
    """run_loop_turn stand-in: answers keyed by the agent that runs (the leaf's, or its escalation's)."""
    seat._workers_model = workers
    seat._loop_runner = types.SimpleNamespace(model=loops)
    agents = []

    def create(agent, title=None, keep=False):
        return "ses_" + agent

    def run(sid, text, agent, **kw):
        agents.append(agent)
        return answers[agent].pop(0)
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session, seat.run_loop_turn = create, run
    return agents


def _work(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}]})
    for _ in range(4):
        evs = [e for e in seat.events_pool.take() if e.get("kind") == "task"]
        if not evs:
            return
        for ev in evs:
            WA._LoopDrainer(seat)._handle(ev)
        settle(seat)


LEAF, ESC = "worker: " + A, GG.escalated_agent(A)


def _results(seat):
    return sorted([d.properties for d in seat.entity_store.docs.values()
                   if (d.properties or {}).get("task") == A and (d.properties or {}).get("outcome")],
                  key=lambda p: p["ts"])


def test_an_empty_leaf_reruns_once_on_the_loops_and_its_result_is_the_tasks(seat):
    agents = _workers(seat, {LEAF: [{"text": "", "usage": U8}], ESC: [{"text": "DONE it landed", "usage": US}]})
    _work(seat)
    assert agents == [LEAF, ESC]
    assert st(seat, A) == "done"
    res = _results(seat)
    assert [r["outcome"] for r in res] == ["stuck", "done"]
    assert res[0]["escalated"] is True and res[0]["usage"] == U8 and res[1]["usage"] == US
    assert seat.entity_store.docs[A].properties["result"] != res[0].get("eid")


def test_a_capped_leaf_reruns_once_then_splits(seat):
    capped = {"text": "", "capped": {"session": "", "split": True}}
    agents = _workers(seat, {LEAF: [dict(capped, usage=U8)], ESC: [dict(capped, usage=US)]})
    _work(seat)
    assert agents == [LEAF, ESC]
    assert st(seat, A) == "split"


def test_a_failed_leaf_reruns_once_and_a_second_failure_fails(seat):
    agents = _workers(seat, {LEAF: [{"text": "FAILED no", "usage": U8}], ESC: [{"text": "FAILED still no",
                                                                                "usage": US}]})
    _work(seat)
    assert agents == [LEAF, ESC]
    assert st(seat, A) == "failed"


def test_a_done_leaf_is_not_escalated(seat):
    agents = _workers(seat, {LEAF: [{"text": "DONE ok", "usage": U8}]})
    _work(seat)
    assert agents == [LEAF] and st(seat, A) == "done"


def test_no_workers_model_no_escalation(seat):
    agents = _workers(seat, {LEAF: [{"text": "", "usage": U8}]}, workers="")
    _work(seat)
    assert agents == [LEAF] and st(seat, A) == "stuck"


def test_workers_on_the_loops_own_model_no_escalation(seat):
    agents = _workers(seat, {LEAF: [{"text": "", "usage": U8}]}, workers=CLAUDE)
    _work(seat)
    assert agents == [LEAF] and st(seat, A) == "stuck"


def test_the_report_counts_escalations_and_both_runs_costs(seat):
    _workers(seat, {LEAF: [{"text": "", "usage": U8}], ESC: [{"text": "DONE it landed", "usage": US}]})
    _work(seat)
    g = next(x for x in GG.ledger(seat.entity_store)["goals"] if x["goal"] == GOAL)
    assert g["escalated"] == 1
    assert g["runs_by_outcome"] == {"done": 1}                      # the task's result, not the first sample
    assert g["tokens"]["qwen3:8b"]["runs"] == 1 and g["tokens"][CLAUDE]["runs"] == 1
    assert g["replanned"] == 0


# ---- every worker result names its model (live: an escalated run's usage was None, so nobody could tell) ----------
def test_a_run_with_no_usage_still_names_its_model(seat):
    agents = _workers(seat, {LEAF: [{"text": ""}], ESC: [{"text": "DONE it landed"}]})
    _work(seat)
    assert agents == [LEAF, ESC]
    res = _results(seat)
    assert [r["model"] for r in res] == ["qwen3:8b", CLAUDE]               # the routed model, as its own property
    assert res[0]["usage"] == {"model": "qwen3:8b", "input": 0, "cache_read": 0, "cache_write": 0, "output": 0,
                               "steps": 0, "estimated": True}
    assert res[1]["usage"]["model"] == CLAUDE and res[1]["usage"]["estimated"] is True


def test_the_runs_own_model_beats_the_routed_one(seat):
    _workers(seat, {LEAF: [{"text": "DONE ok", "model": "qwen3:14b", "usage": dict(U8, model="qwen3:14b")}]})
    _work(seat)
    assert _results(seat)[0]["model"] == "qwen3:14b"


def test_the_ledger_shows_a_usage_less_run_under_its_model(seat):
    _workers(seat, {LEAF: [{"text": ""}], ESC: [{"text": "DONE it landed", "usage": US}]})
    _work(seat)
    g = next(x for x in GG.ledger(seat.entity_store)["goals"] if x["goal"] == GOAL)
    assert g["tokens"]["qwen3:8b"]["runs"] == 1 and g["tokens"]["qwen3:8b"]["estimated"] == 1
    assert g["tokens"][CLAUDE]["runs"] == 1


def test_a_split_run_names_its_model_too(seat):
    _workers(seat, {LEAF: [{"text": "", "capped": {"session": "", "split": True}}]}, workers="qwen3:8b",
             loops="qwen3:8b")
    _work(seat)
    assert _results(seat)[0]["model"] == "qwen3:8b"
