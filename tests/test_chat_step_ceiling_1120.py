"""#1120 (operator 2026-11-11): a person-facing chat answer on the Claude rung takes at most CHAT_STEP_CEILING (12)
acting API steps. Live, one Memo answer ran ~50 steps (3.9M cache-read tokens). The step before the last is told to
make its last step a checkpoint (save the goal, its cards, what's left; say what comes next); a step past the
ceiling is refused and the answer ends capped -- the existing ceiling / Continue machinery (#425). Workers and the
planner keep their own rules. RED first."""
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


def test_the_ceiling_is_the_operators_twelve():
    assert CA.CHAT_STEP_CEILING == 12


def test_a_chat_answer_gets_twelve_acting_steps_then_stops_capped():
    a = _answer("chat")
    for i in range(1, 13):
        a.saw_step(f"m{i}")
        assert _hook(a, f"t{i}") == {}, i
    a.saw_step("m13")
    out = _hook(a, "t13")
    assert out.get("continue_") is False and a.ceiling
    assert "checkpoint" in out["stopReason"].lower()


def test_the_step_before_the_last_is_told_to_checkpoint():
    a = _answer("chat")
    for i in range(1, 11):
        a.saw_step(f"m{i}")
    assert "checkpoint" not in CA._footer(a).lower()
    a.saw_step("m11")
    note = CA._footer(a).lower()
    assert "checkpoint" in note and "last" in note


def test_one_step_over_several_messages_counts_once():
    a = _answer("chat")
    for i in range(1, 13):
        a.saw_step(f"m{i}")
        a.saw_step(f"m{i}")
    assert _hook(a, "t") == {}


def test_other_lanes_are_not_held_to_it():
    for lane in ("loop", "watch"):
        a = _answer(lane)
        for i in range(1, 20):
            a.saw_step(f"m{i}")
        assert _hook(a, "t") == {}, lane


class TestOnTheRung(unittest.TestCase):
    def test_a_long_chat_answer_ends_capped_with_continue(self):
        sdk = scripted_sdk([[("tools", 1)] * 14 + [("text", "done")]])
        with mock.patch.dict(sys.modules, {"claude_agent_sdk": sdk}), \
                mock.patch.object(CA, "find_cli", lambda: "/usr/bin/claude"), key_fixture():
            DR.context(lane="chat")
            try:
                items = list(CA.iter_reply([{"role": "user", "content": "go"}], "claude-agent:sonnet",
                                           stream=False, port=9))
            finally:
                DR.context(None)
        self.assertEqual(len(sdk.tool_runs), 12)
        caps = [v for k, v in items if k == "capped"]
        self.assertTrue(caps)                                           # the chat offers Continue (#425)
        self.assertEqual(caps[0]["steps"], CA.CHAT_STEP_CEILING)


def test_the_last_step_note_is_stripped_where_results_are_stored():
    a = _answer("chat")
    for i in range(1, 12):
        a.saw_step(f"m{i}")
    assert CA._result_text("ok" + CA._footer(a)) == "ok"
