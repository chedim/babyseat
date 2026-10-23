"""Issue #467 (operator, 2026-10-23): ceiling 2048; a calls-left footer on
every membrane tool result; a check-in rides WITH the next tool call; the
stored reply keeps its parts in the order they happened."""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import server as MS  # noqa: E402


def _tool(i, **k):
    return dict({"id": f"t{i}", "name": "query", "input": {"topic": "x"},
                 "output": f"out{i}", "is_error": False}, **k)


def _runner(ladder):
    return MS.TurnRunner(MS.SSEHub(), MS.SessionStore(), MS.MessageStore(), ladder,
                         lambda *a: "ok", system="", model="m", tools=[])


def _seq(out):
    return [(p["type"], p.get("text") if p["type"] == "text" else p.get("callID"))
            for p in out["parts"] if p.get("type") in ("text", "tool")
            and p.get("tool") != "membrane"]


EXPECT = [("text", "Looking."), ("tool", "t1"), ("text", "Then."),
          ("tool", "t2"), ("text", "Done.")]


class TestPartsKeepTheirOrder(unittest.TestCase):
    def test_streamed_text_call_text_call_text(self):
        def ladder(body):
            raise AssertionError("buffered call not used")

        def stream(body, on_event):
            on_event("text", "Looking.")
            on_event("tool", _tool(1))
            on_event("text", "Then.")
            on_event("tool", _tool(2))
            on_event("text", "Done.")
            return {CA.RESP_KEY: CA.PROVIDER, CA.TOOLS_KEY: [_tool(1), _tool(2)],
                    "choices": [{"message": {"content": "Looking.Then.Done."}}],
                    "usage": {}}
        ladder.stream = stream
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        self.assertEqual(_seq(r.run(sid, "go")), EXPECT)

    def test_buffered_reply_is_split_at_the_calls(self):
        def ladder(body):
            return {CA.RESP_KEY: CA.PROVIDER,
                    CA.TOOLS_KEY: [_tool(1, at=8), _tool(2, at=13)],
                    "choices": [{"message": {"content": "Looking.Then.Done."}}],
                    "usage": {}}
        r = _runner(ladder)
        sid = r.sessions.create(title="t")["id"]
        self.assertEqual(_seq(r.run(sid, "go")), EXPECT)

    def test_the_buffered_reader_stamps_where_each_call_came(self):
        def fake_iter(*a, **k):
            yield ("text", "Looking.")
            yield ("tool", _tool(1))
            yield ("text", "Then.")
            yield ("tool", _tool(2))
            yield ("text", "Done.")
        with mock.patch.object(CA, "iter_reply", fake_iter):
            rep = CA.JSONReply([{"role": "user", "content": "hi"}], "m")
            obj = json.loads(rep.read())
        self.assertEqual([t["at"] for t in obj[CA.TOOLS_KEY]], [8, 13])


class TestCeilingAndFooter(unittest.TestCase):
    def test_the_ceiling_is_2048(self):
        self.assertEqual(CA.MAX_TOOL_CALLS_PER_ANSWER, 2048)
        self.assertGreaterEqual(CA.MAX_TURNS, 2 * CA.MAX_TOOL_CALLS_PER_ANSWER)

    def test_a_result_carries_the_calls_left_and_the_checkin_distance(self):
        from membrane import mcp_server as M
        a = CA._Answer()
        a.calls, a.streak = 3, 3
        with mock.patch.dict(M.TOOL_IMPLS, {"query": lambda args, port: "the result"}):
            res = asyncio.run(CA._tool_handler("query", 9, a)({}))
        text = res["content"][0]["text"]
        self.assertTrue(text.startswith("the result"))
        self.assertTrue(text.rstrip().endswith(
            f"· {CA.MAX_TOOL_CALLS_PER_ANSWER - 3} calls left "
            f"· check-in in {CA.CHECKIN_TOOL_CALLS - 3}"), text)

    def test_an_error_result_has_the_footer_too(self):
        from membrane import mcp_server as M

        def boom(args, port):
            raise RuntimeError("nope")
        a = CA._Answer()
        a.calls, a.streak = 1, 1
        with mock.patch.dict(M.TOOL_IMPLS, {"query": boom}):
            res = asyncio.run(CA._tool_handler("query", 9, a)({}))
        self.assertTrue(res["is_error"])
        self.assertIn("calls left", res["content"][0]["text"])

    def test_without_an_answer_the_result_is_unchanged(self):
        from membrane import mcp_server as M
        with mock.patch.dict(M.TOOL_IMPLS, {"query": lambda a, p: "plain"}):
            res = asyncio.run(CA._tool_handler("query", 9)({}))
        self.assertEqual(res["content"][0]["text"], "plain")


class TestCheckinRidesWithTheNextCall(unittest.TestCase):
    def test_the_denial_says_write_it_with_the_next_call(self):
        m = CA.CHECKIN_MESSAGE.lower()
        self.assertIn("same message", m)
        self.assertIn("tool call", m)
        self.assertIn("not as a separate", m)

    def test_the_docs_say_so(self):
        doc = (Path(__file__).resolve().parent.parent / "docs" / "claude-agent.md").read_text()
        self.assertIn("same message as its next tool call", doc)


if __name__ == "__main__":
    unittest.main()
