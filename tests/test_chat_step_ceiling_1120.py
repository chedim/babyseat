"""A person-facing chat answer on the Claude rung has no step ceiling (the operator, 2026-11-12: the 12-step limit
of #1120 was annoying, the Continue button with it). The per-answer tool-call ceiling (#425) and a goal worker's
one-step rule (#1116) stay. RED first: this was the 12-step ceiling's test."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import diag_requests as DR  # noqa: E402
from test_claude_agent_384 import key_fixture  # noqa: E402
from test_claude_agent_checkin_425 import scripted_sdk  # noqa: E402


def _answer(lane):
    with mock.patch.object(DR, "current", lambda: {"lane": lane}):
        return CA._Answer()


def _hook(a, tid):
    return asyncio.run(a.hook({"tool_use_id": tid}))


def test_there_is_no_chat_step_ceiling():
    assert not hasattr(CA, "CHAT_STEP_CEILING") and not hasattr(CA, "CHAT_STEP_MESSAGE")


def test_a_chat_answer_runs_any_number_of_steps():
    a = _answer("chat")
    for i in range(1, 60):
        a.saw_step(f"m{i}")
        assert _hook(a, f"t{i}") == {}, i
    assert "checkpoint" not in CA._footer(a).lower()


def test_the_tool_call_ceiling_still_holds_in_chat():
    a = _answer("chat")
    a.calls = CA.MAX_TOOL_CALLS_PER_ANSWER
    assert _hook(a, "t").get("continue_") is False and a.ceiling


class TestOnTheRung(unittest.TestCase):
    def test_a_long_chat_answer_is_not_capped(self):
        sdk = scripted_sdk([[("tools", 1)] * 30 + [("text", "done")]])
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": sdk}), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), key_fixture():
            DR.context(lane="chat")
            try:
                items = list(CA.iter_reply([{"role": "user", "content": "go"}], "claude-agent:sonnet",
                                           stream=False, port=9))
            finally:
                DR.context(None)
        self.assertEqual(len(sdk.tool_runs), 30)
        self.assertFalse([v for k, v in items if k == "capped"])
