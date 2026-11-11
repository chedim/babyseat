"""#1120, the experiment the operator approved (2026-11-11) and its measurement:
1. a `workers` model for the goal graph's leaf tasks (the worker lane), apart from `loops` (watch loops and the
   planner); unset = the loops' pick, so nothing changes until it is set;
2. the cap mark is `capped` (local leaves carry it too), the old `claudeCapped` still read;
3. a per-goal ledger: each worker run's tokens on its result entity, each planner run's summed on the goal, and a
   read-only report (/membrane/diag/goals-ledger). RED first."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag as DIAG  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import minds_models as MM  # noqa: E402
from membrane import opencode as OC  # noqa: E402
from membrane import options as O  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402
from test_background_work_minds import CLAUDE, _runner, _session, _Srv  # noqa: E402
from test_goal_graph_738 import GOAL, seat, settle, st, write  # noqa: E402,F401

WORKER = "worker: " + GG.task_eid(GOAL, "a")
LOOP = "watch: membrane::goal:abc"
PLANNER = "planner: " + GOAL
A, B = GG.task_eid(GOAL, "a"), GG.task_eid(GOAL, "b")


# ---- 1. the workers model --------------------------------------------------------------------------------------
class TestTheWorkersModel(unittest.TestCase):
    def setUp(self):
        self.srv = _Srv(tempfile.mkdtemp())
        self.r, self.bodies = _runner(self.srv)

    def _ran(self, agent):
        sid = _session(self.r, agent, "")
        self.r.run(sid, "go")
        return self.bodies[-1]["model"]

    def test_unset_a_leaf_runs_on_the_loops_pick(self):
        self.assertEqual(MM.read(self.srv)["workers"], "")
        self.assertEqual(self._ran(WORKER), "qwen3:8b")

    def test_set_leaves_run_on_it_and_the_loops_and_planner_do_not(self):
        MM.apply(self.srv, workers="qwen3:14b")
        self.assertEqual(self._ran(WORKER), "qwen3:14b")
        self.assertEqual(self._ran(LOOP), "qwen3:8b")
        self.assertEqual(self._ran(PLANNER), "qwen3:8b")

    def test_it_persists_and_is_reapplied_at_boot_and_clears_to_unset(self):
        MM.apply(self.srv, workers="qwen3:14b")
        self.assertEqual(json.load(open(self.srv._models_path))["workers"], "qwen3:14b")
        fresh = _Srv(Path(self.srv._models_path).parent)
        _runner(fresh)
        MM.load(fresh)
        self.assertEqual(MM.read(fresh)["workers"], "qwen3:14b")
        MM.apply(self.srv, workers="")
        self.assertEqual(MM.read(self.srv)["workers"], "")
        self.assertEqual(self._ran(WORKER), "qwen3:8b")

    def test_claude_on_the_workers_needs_its_own_pick(self):
        MM.apply(self.srv, loops=CLAUDE)                 # the loops on Claude says nothing about the leaves
        MM.apply(self.srv, workers="qwen3:8b")
        self.assertEqual(self._ran(WORKER), "qwen3:8b")
        MM.apply(self.srv, workers=CLAUDE, workers_auth="api-key")
        sid = _session(self.r, WORKER, "")
        self.r.run(sid, "go")
        self.assertEqual(self.bodies[-1]["model"], CLAUDE)
        self.assertEqual(self.bodies[-1][CA.AUTH_KEY], CA.MODE_KEY)

    def test_the_options_window_reads_and_writes_it(self):
        self.assertIn("workers", O.MIND_KEYS)
        self.assertIn("workers_auth", O.MIND_KEYS)
        bg = O.read(self.srv)["background"]
        self.assertIn("workers", bg)
        self.assertIn("workers", O.read(self.srv)["writable"])


# ---- 2. the cap mark ---------------------------------------------------------------------------------------------
def test_the_cap_mark_is_neutral_and_the_old_one_is_still_read():
    assert CA.CAP_MARK == "capped"
    assert CA.cap_of({"capped": {"session": "s"}}) == {"session": "s"}
    assert CA.cap_of({"claudeCapped": {"session": "old"}}) == {"session": "old"}    # rows stored before #1120
    assert CA.cap_of({}) is None


def test_a_loop_turn_reports_a_cap_under_either_name():
    for key in ("capped", "claudeCapped"):
        out = OC.loop_turn_out({"info": {key: {"session": "", "split": True}, "modelID": "m"},
                                "parts": [{"type": "text", "text": "x"}]})
        assert isinstance(out.get("capped"), dict), key


# ---- 3. the ledger: what each run cost ---------------------------------------------------------------------------
def test_a_loop_turn_reports_its_usage():
    out = OC.loop_turn_out({"info": {"modelID": "qwen3:14b", "steps": 2,
                                     "tokens": {"total": 130, "input": 100, "output": 30, "reasoning": 0,
                                                "cache": {"read": 60, "write": 10}}},
                            "parts": [{"type": "text", "text": "DONE ok"}]})
    assert out["usage"] == {"model": "qwen3:14b", "input": 30, "cache_read": 60, "cache_write": 10,
                            "output": 30, "steps": 2, "estimated": False}


def test_a_turn_counts_its_model_steps():
    srv = _Srv(tempfile.mkdtemp())
    r, _ = _runner(srv)
    sid = _session(r, "build", "")
    out = r.run(sid, "hi")
    assert out["info"]["steps"] == 1


def test_a_claude_answer_reports_its_api_steps():
    from test_claude_agent_384 import key_fixture
    from unittest import mock

    async def consume(sdk, messages, model, stream, put, port=None, resume=None, env=None):
        for i in range(3):                               # three API steps, as the SDK reports them
            put(("step", {"usage": {"input_tokens": 5, "output_tokens": 1}, "first": i == 0}))
        put(("usage", {"prompt_tokens": 15, "completion_tokens": 3}))
    with mock.patch.object(CA, "_consume", consume), key_fixture():
        items = list(CA.iter_reply([{"role": "user", "content": "go"}], "claude-agent:sonnet",
                                   stream=False, sdk=object(), port=9))
    usage = [v for k, v in items if k == "usage"][-1]
    assert usage["steps"] == 3


USAGE = {"model": "qwen3:8b", "input": 10, "cache_read": 0, "cache_write": 0, "output": 5, "steps": 1,
         "estimated": False}


def _workers(seat, answers):
    def create(agent, title=None, keep=False):
        return "ses_" + agent

    def run(sid, text, agent, **kw):
        if agent.startswith("planner"):
            return {"text": "UNCHANGED", "usage": dict(USAGE, model="claude-agent:sonnet", input=100)}
        task = next(t for t in answers if agent.endswith(": " + t))
        return answers[task].pop(0)
    seat.loops = WA.LoopRunner(3)
    seat.create_loop_session, seat.run_loop_turn = create, run


def _work(seat, n=6):
    for _ in range(n):
        evs = [e for e in seat.events_pool.take() if e.get("kind") == "task"]
        if not evs:
            return
        for ev in evs:
            WA._LoopDrainer(seat)._handle(ev)
        settle(seat)


def test_a_worker_run_writes_its_usage_on_its_result(seat):
    _workers(seat, {A: [{"text": "DONE a", "usage": USAGE}]})
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}]})
    _work(seat)
    res = seat.entity_store.docs[A].properties["result"]
    assert seat.entity_store.docs[res].properties["usage"] == USAGE


def test_a_capped_worker_run_writes_its_usage_too(seat):
    _workers(seat, {A: [{"text": "", "capped": {"session": "", "split": True}, "usage": USAGE}]})
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}]})
    _work(seat)
    assert st(seat, A) == "split"
    res = seat.entity_store.docs[A].properties["result"]
    assert seat.entity_store.docs[res].properties["usage"] == USAGE


def test_planner_runs_sum_on_the_goal(seat):
    _workers(seat, {})
    for _ in range(2):
        WA._LoopDrainer(seat)._lane({"entity": GOAL, "prompt": "plan the goal: x"}, "planner")
    led = seat.entity_store.docs[GOAL].properties["planner_usage"]
    assert led == {"claude-agent:sonnet": {"runs": 2, "input": 200, "cache_read": 0, "cache_write": 0,
                                           "output": 10, "steps": 2, "estimated": 0}}


def test_the_report_per_goal(seat):
    _workers(seat, {A: [{"text": "STUCK need x", "usage": USAGE}, {"text": "DONE a", "usage": USAGE}],
                    B: [{"text": "", "capped": {"session": ""}, "usage": dict(USAGE, output=7)}]})
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}, {"key": "b"}]})
    _work(seat)
    write(seat, GOAL, "plan", {"tasks": [{"key": "a"}]})            # the stuck one listed again: re-planned
    _work(seat)
    WA._LoopDrainer(seat)._lane({"entity": GOAL, "prompt": "plan the goal: x"}, "planner")
    rep = GG.ledger(seat.entity_store)
    g = next(x for x in rep["goals"] if x["goal"] == GOAL)
    assert g["open"] is True
    assert g["runs_by_outcome"] == {"stuck": 1, "done": 1, "split": 1}
    assert g["tasks_by_status"]["done"] == 1 and g["tasks_by_status"]["split"] == 1
    assert g["replanned"] == 1                                       # a ran twice
    assert g["leaves"] == 2 and g["runs"] == 3 and g["runs_per_leaf"] == 1.5
    assert g["tokens"]["qwen3:8b"] == {"runs": 3, "input": 30, "cache_read": 0, "cache_write": 0,
                                       "output": 17, "steps": 3, "estimated": 0}
    assert g["tokens"]["claude-agent:sonnet"]["input"] == 100
    assert g["total"]["input"] == 130
    assert "reopen" in g["held"]


def test_the_report_is_a_diag_route():
    assert "/membrane/diag/goals-ledger" in DIAG.DIAG_PATHS


def test_the_options_window_shows_the_leaf_tasks_pick():
    import shutil
    import subprocess
    if not shutil.which("node"):
        return
    from test_options_background_work import BG, UI

    def rows(bg):
        js = "const H=require(%r); process.stdout.write(JSON.stringify(H.backgroundRows(%s)))" % (
            str(UI / "home.js"), json.dumps(bg))
        return json.loads(subprocess.check_output(["node", "-e", js]))
    assert 'data-opt="workers"' not in rows(BG)                     # a seat from before #1120
    html = rows(dict(BG, workers="", workers_auth="chat"))
    assert 'data-opt="workers"' in html and "Leaf tasks" in html
    assert '<option value="" selected>same as the Goal worker</option>' in html
    assert 'value="qwen3:14b" selected' in rows(dict(BG, workers="qwen3:14b", workers_auth="chat"))


def test_a_turn_on_the_seats_token_estimate_is_marked_estimated():
    import membrane.server as MS
    a_info = {}
    MS._tokens_fill(a_info, {"usage": {"prompt_tokens": 9, "completion_tokens": 2, "estimated": True}})
    assert a_info["tokensEstimated"] is True and a_info["steps"] == 1
    b_info = {}
    MS._tokens_fill(b_info, {"usage": {"prompt_tokens": 9, "completion_tokens": 2}})
    assert "tokensEstimated" not in b_info
