"""membrane/capture.py + the seat hooks: events from live turns, the call
id joining a call to its result, duplicates collapsing, discourse kept on
mixed turns, and the chat path protected from every failure."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

from membrane import efield as EF  # noqa: E402
from membrane.capture import Capturer, LazyEmbedder, Sessions  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

CALLS = {0: {"id": "call_1", "function": {"name": "edit",
                                          "arguments": '{"filePath": "membrane/router.py", "newString": "x"}'}}}


def cap(mode="shadow"):
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    return Capturer(f, mode=mode, start=False), f


def events(f):
    return [d for _, d in f.b.scan("events")]


class TestCapturer(unittest.TestCase):
    def test_turn_calls_and_result_become_events(self):
        c, f = cap()
        c.on_turn("fix the router please", "Editing router.py now.", "Need the file first.")
        c.on_calls("fix the router please", CALLS)
        c.on_tool_result("call_1", "edit", CALLS[0]["function"]["arguments"],
                         "Edit applied successfully.", "fix the router please")
        c.flush()
        evs = events(f)
        roles = sorted((d["stream"], d["role"]) for d in evs)
        self.assertEqual(roles, [("discourse", "model"), ("discourse", "reasoning"),
                                 ("discourse", "user"), ("observation", "tool")])
        obs = next(d for d in evs if d["stream"] == "observation")
        self.assertEqual((obs["outcome"], obs["args"]["filePath"]), ("applied", "membrane/router.py"))
        self.assertEqual(obs["served"], "fix the router please")

    def test_duplicates_collapse(self):
        c, f = cap()
        for _ in range(3):                       # streaming captures calls twice;
            c.on_calls("q", CALLS)               # every tool round re-sends the ask
            c.on_turn("fix the router please")
        c.flush()
        self.assertEqual(len(events(f)), 2)

    def test_off_writes_nothing(self):
        c, f = cap("off")
        c.on_turn("hello there friend")
        c.flush()
        self.assertEqual(events(f), [])

    def test_on_applies(self):
        c, f = cap("on")
        c.on_turn("please update membrane/router.py for the gateway")
        c.flush()
        self.assertEqual(f.get("f:membrane/router.py").tier, "live")

    def test_failures_never_reach_the_caller(self):
        c, f = cap()
        with mock.patch.object(f, "write_events", side_effect=RuntimeError("boom")):
            c.on_turn("hello there friend")
            c._t = None
            c.flush()
        self.assertEqual(c.errors, 1)

    def test_a_full_queue_drops_discourse_not_observations(self):
        f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        c = Capturer(f, mode="shadow", start=False, maxsize=1)
        c.on_turn("first message here")
        c.on_turn("second message dropped")        # full: discourse dropped
        c.on_calls("q", CALLS)                     # full: oldest evicted, obs kept
        c.flush()
        kinds = [d["stream"] for d in events(f)]
        self.assertEqual(kinds, ["observation"])

    def test_sessions_advance_after_a_gap(self):
        s = Sessions(boot=100, gap_s=10)
        self.assertEqual(s.current(200), "s:100:0")
        self.assertEqual(s.current(205), "s:100:0")
        self.assertEqual(s.current(300), "s:100:1")

    def test_lazy_embedder(self):
        class M:
            embedder = None
        m = M()
        le = LazyEmbedder(m)
        with self.assertRaises(RuntimeError):
            le.embed(["x"])
        m.embedder = FakeEmbedder()
        self.assertEqual(le.model, "fake-embed")
        self.assertEqual(len(le.embed(["a b"])), 1)


class TestSeatHooks(unittest.TestCase):
    def test_tool_results_reach_the_capturer_unclipped(self):
        from membrane import opencode as oc

        class Mem:
            _pending_tools = {"call_1": {"query": "q", "name": "bash", "args": '{"command": "ls"}'}}
            server_ref = None
            fork_owns_field = True               # keep the exemplar capture quiet

        class Srv:
            membrane = Mem()
            _tool_results = {}
            capturer = mock.Mock()
        big = "x" * 9000
        body = {"messages": [{"role": "tool", "tool_call_id": "call_1", "content": big}]}
        oc._capture_tool_results(Srv, body)
        Srv.capturer.on_tool_result.assert_called_once()
        self.assertEqual(Srv.capturer.on_tool_result.call_args[0][3], big)

    def test_calls_reach_the_capturer(self):
        from membrane import opencode as oc
        srv = mock.Mock()
        mem = mock.Mock(server_ref=srv, _pending_tools={})
        with mock.patch.object(oc, "_codegraph_note_dirty"):
            oc._capture_plan_and_calls(mem, "q", "", CALLS)
        srv.capturer.on_calls.assert_called_once_with("q", CALLS)


if __name__ == "__main__":
    unittest.main()
