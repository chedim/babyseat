"""#1116: the goal graph splits recursively. A task is a leaf when its worker finishes it in ONE step; a worker
that can't ends the task `split`, the planner plans it into sub-tasks (`parent`), any depth, and a split task is
done when its sub-tasks are. The same Task class, blocker edges, cascade and planner lane as #738. RED first."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag_requests as DR  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import server as MS  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from test_claude_agent_384 import key_fixture  # noqa: E402
from test_claude_agent_checkin_425 import scripted_sdk  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401

A, B = GG.task_eid(GOAL, "a"), GG.task_eid(GOAL, "b")
A1, A2, A21 = GG.task_eid(GOAL, "a1"), GG.task_eid(GOAL, "a2"), GG.task_eid(GOAL, "a21")
PLAN = {"tasks": [{"key": "a", "title": "do a"}, {"key": "b", "title": "do b", "after": ["a"]}]}


def _workers(seat, answers):
    """run_loop_turn stand-in: each worker run takes the next answer for its task ({"text": ...} or a capped one)."""
    runs = []

    def create(agent, title=None, keep=False):
        return "ses_" + agent

    def run(sid, text, agent, **kw):
        runs.append(agent)
        task = next(t for t in answers if agent.endswith(": " + t))
        return answers[task].pop(0)
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session, seat.run_loop_turn = create, run
    return runs


def _work(seat, n=8):
    """Run every task the pool holds (and what their endings release), up to n passes."""
    for _ in range(n):
        evs = [e for e in seat.events_pool.take() if e.get("kind") == "task"]
        if not evs:
            return
        for ev in evs:
            WA._LoopDrainer(seat)._handle(ev)
        settle(seat)


CAPPED = {"text": "", "capped": {"session": "sdk-1", "calls": 3, "progress": "- bash ls -> completed: ok"}}


# ---- the stop rule on the graph: a worker that can't finish in one step ends the task split -------------------
def test_a_worker_cut_at_its_one_step_ends_the_task_split_and_does_not_go_on(seat):
    runs = _workers(seat, {A: [CAPPED, {"text": "DONE never asked"}]})
    write(seat, GOAL, "plan", PLAN)
    _work(seat)
    assert st(seat, A) == "split"
    assert len(runs) == 1                                   # no second chunk, no second session
    assert "ls" in seat.entity_store.docs[A].properties["receipt"]
    assert st(seat, B) == "open"                            # a split task still blocks what waits on it


def test_a_split_task_asks_its_goal_to_plan_it(seat):
    _workers(seat, {A: [CAPPED]})
    write(seat, GOAL, "plan", PLAN)
    _work(seat)
    assert any("split" in c["prompt"] and A in c["prompt"] for c in seat.calls), seat.calls


def test_a_worker_can_say_split_itself():
    assert GG.parse_worker("looked at it\nSPLIT: three files, each its own edit") == \
        ("split", "three files, each its own edit")


# ---- the plan: sub-tasks under a task, any depth ---------------------------------------------------------------
def test_sub_tasks_run_and_their_parent_is_done_when_they_are(seat):
    _workers(seat, {A: [CAPPED], A1: [{"text": "DONE a1 landed"}], A2: [{"text": "DONE a2 landed"}],
                    B: [{"text": "DONE b landed"}]})
    write(seat, GOAL, "plan", PLAN)
    _work(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a1", "title": "a, part 1", "parent": "a"},
                                         {"key": "a2", "title": "a, part 2", "parent": "a", "after": ["a1"]}]})
    assert seat.entity_store.docs[A1].properties["parent"] == A
    assert st(seat, A1) == "owed" and st(seat, A2) == "open" and st(seat, A) == "split"
    _work(seat)
    assert st(seat, A1) == "done" and st(seat, A2) == "done"
    assert st(seat, A) == "done"                            # done because its sub-tasks are
    assert st(seat, B) == "done"                            # and what waited on it ran


def test_a_sub_task_splits_again_and_the_tree_closes_bottom_up(seat):
    _workers(seat, {A: [CAPPED], A1: [{"text": "DONE a1"}], A2: [CAPPED], A21: [{"text": "DONE a21"}],
                    B: [{"text": "DONE b"}]})
    write(seat, GOAL, "plan", {"tasks": PLAN["tasks"]})
    _work(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a1", "parent": "a"}, {"key": "a2", "parent": "a"}]})
    _work(seat)
    assert st(seat, A2) == "split" and st(seat, A) == "split" and st(seat, B) == "open"
    write(seat, GOAL, "plan", {"tasks": [{"key": "a21", "parent": "a2"}]})
    _work(seat)
    assert [st(seat, t) for t in (A21, A2, A, B)] == ["done", "done", "done", "done"]


def test_the_planner_can_split_a_task_before_it_runs(seat):
    runs = _workers(seat, {A1: [{"text": "DONE a1"}], A: [{"text": "DONE a as one"}], B: [{"text": "DONE b"}]})
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}, {"key": "a1", "parent": "a"}, {"key": "b", "after": ["a"]}]})
    assert st(seat, A) == "split"
    _work(seat)
    assert not any(r.endswith(": " + A) for r in runs)      # the parent itself never went to a worker
    assert st(seat, A) == "done" and st(seat, B) == "done"


def test_a_parent_whose_sub_tasks_were_all_dropped_is_dropped_too(seat):
    # operator 2026-11-11: every piece dropped -> the task itself was dropped (not done, not left split)
    _workers(seat, {A: [CAPPED]})
    write(seat, GOAL, "plan", PLAN)
    _work(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a1", "parent": "a"}, {"key": "a2", "parent": "a"}]})
    write(seat, GOAL, "plan", {"drop": ["a1"]})
    assert st(seat, A) == "split"                           # a2 still open: not yet
    write(seat, GOAL, "plan", {"drop": ["a2"]})
    assert st(seat, A) == "dropped"


def test_a_parent_with_one_done_and_the_rest_dropped_is_done(seat):
    _workers(seat, {A: [CAPPED], A1: [{"text": "DONE a1"}], B: [{"text": "DONE b"}]})
    write(seat, GOAL, "plan", PLAN)
    _work(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a1", "parent": "a"}, {"key": "a2", "parent": "a"}]})
    write(seat, GOAL, "plan", {"drop": ["a2"]})
    _work(seat)
    assert st(seat, A) == "done"


def test_bad_parents_are_refused_whole(seat):
    write(seat, GOAL, "plan", PLAN)
    est = seat.entity_store
    for plan, why in (({"tasks": [{"key": "x", "parent": "nope"}]}, "parent"),
                      ({"tasks": [{"key": "x", "parent": "x"}]}, "parent"),
                      ({"tasks": [{"key": "x", "parent": "y"}, {"key": "y", "parent": "x"}]}, "parent")):
        r = GG.apply_plan(seat, GOAL, plan)
        assert not r["ok"] and why in r["error"], r
        assert GG.task_eid(GOAL, "x") not in est.docs
    GG.take(seat, A)                                        # running: its worker is still on it
    r = GG.apply_plan(seat, GOAL, {"tasks": [{"key": "x", "parent": "a"}]})
    assert not r["ok"] and "running" in r["error"], r


def test_the_graph_and_the_prompts_show_the_tree(seat):
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}, {"key": "a1", "parent": "a"}]})
    v = GG.view(seat.entity_store, GOAL)
    assert {t["eid"]: t.get("parent") for t in v["tasks"]}[A1] == A
    text = GG.planner_text(seat.entity_store, GOAL, "task x split")
    assert "parent" in text and "split" in text
    assert "SPLIT" in GG.worker_text(seat.entity_store, A1)


# ---- "one step" on the Claude rung: one acting step (one batch of tool calls), then the verdict --------------
class TestOneStepOnTheClaudeRung(unittest.TestCase):
    def _run(self, lane, plan):
        sdk = scripted_sdk([plan])
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": sdk}), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), key_fixture():
            CA.reset_stats()
            DR.context(lane=lane)
            try:
                items = list(CA.iter_reply([{"role": "user", "content": "go"}], "claude-agent:sonnet",
                                           stream=False, port=9))
            finally:
                DR.context(None)
        return sdk, [i for i in items if i[0] == "capped"]

    def test_one_batch_of_calls_then_the_verdict_is_one_step(self):
        sdk, caps = self._run("worker", [("tools", 3), ("text", "DONE ok")])
        self.assertEqual(len(sdk.tool_runs), 3)
        self.assertFalse(caps)

    def test_a_second_batch_of_calls_is_refused_and_the_answer_ends_capped(self):
        sdk, caps = self._run("worker", [("tools", 2), ("tools", 2), ("text", "DONE ok")])
        self.assertEqual(len(sdk.tool_runs), 2)
        self.assertTrue(caps)
        self.assertTrue(any("split" in s for s in sdk.seen_by_model), sdk.seen_by_model)

    def test_other_lanes_keep_many_steps(self):
        sdk, caps = self._run("chat", [("tools", 2), ("tools", 2), ("text", "done")])
        self.assertEqual(len(sdk.tool_runs), 4)
        self.assertFalse(caps)

    def test_a_step_split_over_several_messages_is_still_one_step(self):
        """The CLI sends one AssistantMessage per content block, all with the step's message id."""
        a = CA._Answer.__new__(CA._Answer)
        with mock.patch.object(DR, "current", lambda: {"lane": "worker"}):
            a.__init__()
        a.saw_step("msg_1")
        a.saw_step("msg_1")
        out = asyncio.run(a.hook({"tool_use_id": "t2"}))
        self.assertEqual(out, {})
        a.saw_step("msg_2")
        out = asyncio.run(a.hook({"tool_use_id": "t3"}))
        self.assertEqual(out.get("continue_"), False)


# ---- "one step" on a local model (the TurnRunner's rounds) ----------------------------------------------------
def _runner_rounds(*msgs):
    state = {"n": 0}

    def ladder(body):
        raise AssertionError("streamed lane only")

    def stream(body, on_event):
        msg = msgs[min(state["n"], len(msgs) - 1)]
        state["n"] += 1
        return {"choices": [{"message": msg, "finish_reason": "stop"}]}
    ladder.stream = stream
    ran = []
    r = MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                      lambda name, args, cid=None: ran.append(cid) or "ok", system="", model="m",
                      tools=[{"name": "entity", "description": "", "inputSchema": {"type": "object"}}])
    return r, ran


def _call(cid):
    return {"id": cid, "type": "function", "function": {"name": "entity", "arguments": "{}"}}


def _turn(agent, *msgs):
    r, ran = _runner_rounds(*msgs)
    sid = r.sessions.create(title=agent)["id"]
    r.sessions.sessions[sid]["agent"] = agent
    out = r.run(sid, "go")
    return out, ran


def test_a_local_worker_gets_one_acting_round():
    out, ran = _turn("worker: " + A, {"tool_calls": [_call("c1"), _call("c2")]},
                     {"tool_calls": [_call("c3")]}, {"content": "DONE"})
    assert ran == ["c1", "c2"]
    assert isinstance((out.get("info") or {}).get(CA.CAP_MARK), dict)


def test_a_local_worker_that_acts_once_and_answers_is_not_capped():
    out, ran = _turn("worker: " + A, {"tool_calls": [_call("c1")]}, {"content": "DONE it"})
    assert ran == ["c1"]
    assert not (out.get("info") or {}).get(CA.CAP_MARK)


def test_a_local_chat_keeps_its_rounds():
    out, ran = _turn("build", {"tool_calls": [_call("c1")]}, {"tool_calls": [_call("c2")]}, {"content": "done"})
    assert ran == ["c1", "c2"]


# ---- the doctrine: long answers checkpoint to the field and end ------------------------------------------------
def test_the_doctrine_says_long_work_checkpoints_and_ends():
    text = (Path(GG.__file__).resolve().parent / "help" / "goals.md").read_text().lower()
    assert "checkpoint" in text and "end the answer" in text


def test_a_split_task_can_be_dropped(seat):
    _workers(seat, {A: [CAPPED]})
    write(seat, GOAL, "plan", PLAN)
    _work(seat)
    write(seat, GOAL, "plan", {"drop": ["a"]})
    assert st(seat, A) == "dropped"
