"""THE THINKING LOOPS AT BOOT (the operator's 2026-10-02 design, red
first): the watch-events queue DRAINS into the loop pool (3 concurrent
non-operator loops + 1 dedicated operator lane); each loop = ONE REAL
turn against the seat's own /v1 (loopback: the gate, recall, MCP
tools, capture all run), in a CHILD SESSION whose agent NAME carries
the processed entity's id ('watch: f:membrane/router.py' -- the
frontend renders it as a background subagent task); the model REACTS
or IGNORES; the verdict stamps the entity (watch_last_decision) and
the loop's exchange learns like any turn. The banner gains the
'loops:' line."""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import watch_agents as WA  # noqa: E402
from membrane import opencode as OC  # noqa: E402


class TestLoopPoolWiring(unittest.TestCase):
    def test_boot_wires_pool_drain(self):
        """THE DRAIN: the boot's loop-drainer thread takes the events
        queue and runs one turn per fire (admit-limited): a fire
        becomes a loop; the operator lane's events ride dedicated."""
        class _Srv:
            def __init__(self):
                self.events_pool = WA.WatchEvents()
                self.entity_store = None
                self.loops = WA.LoopRunner(max_loops=3)
                self.loop_sessions = []
                self.loop_runs = []

            def run_loop_turn(self, session_id, text, agent):
                self.loop_runs.append((session_id, agent, text[:40]))
                return {"verdict_text": "ignored: nothing to act on"}

            def create_loop_session(self, agent, title):
                sid = f"ses_loop{len(self.loop_sessions)}"
                self.loop_sessions.append(sid)
                return sid
        srv = _Srv()
        srv.events_pool.put({"event_id": "ev1", "kind": "job-done",     # #729: watch kinds triage
                             "entity": "membrane::f:membrane/router.py",
                             "ts": time.time()})
        drainer = WA.start_loop_drainer(srv)
        deadline = time.time() + 5
        while not srv.loop_runs and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(len(srv.loop_runs), 1)
        agent, = [r[1] for r in srv.loop_runs]
        self.assertIn("f:membrane/router.py", agent)
        drainer.stop()

    def test_pool_admission_limits_to_three(self):
        """A burst: 6 fires queued; at most 3 loops run at once; the
        rest wait their turn (the pool's admit discipline at the drain
        seam)."""
        class _Srv:
            def __init__(self):
                self.events_pool = WA.WatchEvents()
                self.loops = WA.LoopRunner(max_loops=3)
                self.running = 0
                self.max_running = 0

            def run_loop_turn(self, session_id, text, agent):
                with self.loops._lk:
                    self.running += 1
                    self.max_running = max(self.max_running,
                                           self.running)
                time.sleep(0.15)
                with self.loops._lk:
                    self.running -= 1
                return {"verdict_text": "ignored"}

            def create_loop_session(self, agent, title):
                return f"ses_{agent}"

        srv = _Srv()
        for i in range(6):
            srv.events_pool.put({"event_id": f"ev{i}", "kind": "around",
                                 "entity": f"membrane::f:x{i}.py",
                                 "ts": time.time()})
        drainer = WA.start_loop_drainer(srv)
        deadline = time.time() + 8
        while len(srv.loops._held) and time.time() < deadline:
            time.sleep(0.1)
        self.assertLessEqual(srv.max_running, 3)
        drainer.stop()

    def test_operator_events_bypass_the_pool(self):
        """THE OPERATOR LANE: an operator-message fire never occupies a
        loop worker -- the main chat's turn IS its reaction (the drain
        skips it; the queue keeps the audit)."""
        skip = WA.is_operator_event({"kind": "operator-message"})
        self.assertTrue(skip)
        self.assertFalse(WA.is_operator_event({"kind": "away"}))
        self.assertFalse(WA.is_operator_event({"kind": "peer-miss"}))


class TestLoopSessionShape(unittest.TestCase):
    def test_session_title_carries_entity_id(self):
        """THE AGENT NAME: 'watch: <entity key WITHOUT the namespace>'
        -- the operator's ask (the eid's kind:value tail is the
        frontend's row name; the namespace is implicit)."""
        name = WA.loop_agent_name("watch", "membrane::f:membrane/router.py")
        self.assertIn("f:membrane/router.py", name)
        self.assertTrue(name.startswith("watch"))

    def test_task_text_carries_react_or_ignore(self):
        """THE TASK: the loop's turn text describes the event + the
        react-or-ignore covenant (the model's choice is the turn)."""
        text = WA.loop_task_text({"kind": "away",
                                  "entity": "membrane::f:x.py",
                                  "distance": 1.0, "prev": 0.0})
        self.assertIn("f:x.py", text)
        self.assertIn("react", text.lower())
        self.assertIn("ignore", text.lower())


class TestBannerLoopsLine(unittest.TestCase):
    def test_banner_carries_loops_line(self):
        """THE BANNER'S AMBIENT LOAD: 'loops: 2 running (...), 1
        queued' -- every seat sees the mind's load."""
        from unittest import mock

        class _Srv:
            def __init__(self):
                self.loops = WA.LoopRunner(max_loops=3)
                self.loops.titles = ["watch: f:router.py",
                                     "watch: w:gateway"]
                self.events_pool = WA.WatchEvents()
        srv = _Srv()
        # 2 loops actually HELD (the count is the pool's, not a mock)
        loop_titles = ["watch: f:router.py", "watch: w:gateway"]
        d1 = WA.LoopDesc("watch", eid="a"); d2 = WA.LoopDesc("watch", eid="b")
        srv.loops.admit(d1); srv.loops.admit(d2)
        with mock.patch.object(WA, "_loop_titles", create=True,
                               side_effect=lambda s: loop_titles):
            line = WA.loops_banner_line(srv)
        self.assertIsNotNone(line)
        self.assertIn("loops:", line)
        self.assertIn("2 running", line)
        srv2 = _Srv()
        srv2.events_pool.put({"event_id": "e", "kind": "away",
                              "entity": "x", "ts": time.time()})
        with mock.patch.object(WA, "_loop_titles", create=True,
                               side_effect=lambda s: []):
            line2 = WA.loops_banner_line(srv2)
        self.assertIn("1 queued", line2)
        # nothing running/queued: the line is absent (noise-free)
        with mock.patch.object(WA, "_loop_titles", create=True,
                               side_effect=lambda s: []):
            self.assertIsNone(WA.loops_banner_line(_Srv()))


if __name__ == "__main__":
    unittest.main()