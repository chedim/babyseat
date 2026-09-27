"""The seat card (opencode._seat_card): this session's switches rendered
for the model -- mode-specific, tool list from the MCP registry, never
claiming a state the seat is not in."""

import unittest

from membrane.opencode import _seat_card, _mcp_tool_lines
from membrane.mcp_server import TOOLS


class _Router:
    def __init__(self, budget):
        self.cloud_budget = budget


class _Srv:
    def __init__(self, **kw):
        self.no_context = False
        self.tool_keep = 3
        self.replay_sim = 0.0
        self.agent_writes = True
        self.global_field = None
        self.router = None
        self.receipts = True
        self.__dict__.update(kw)


class TestSeatCard(unittest.TestCase):
    def test_no_context_mode_is_explained(self):
        card = _seat_card(_Srv(no_context=True, tool_keep=2), mcp_on=True)
        self.assertIn("FIELD-ONLY (--no-context)", card)
        self.assertIn("last 2 tool exchanges", card)
        self.assertIn("MISSING EARLIER CONTEXT is expected", card)

    def test_full_context_mode_says_so(self):
        card = _seat_card(_Srv(), mcp_on=True)
        self.assertIn("CONTEXT: full transcript", card)
        self.assertNotIn("--no-context", card)

    def test_elision_line_absent_when_disabled(self):
        card = _seat_card(_Srv(no_context=True, tool_keep=-1), mcp_on=True)
        self.assertNotIn("IN-TURN ELISION", card)

    def test_every_mcp_tool_is_listed_when_mounted(self):
        card = _seat_card(_Srv(), mcp_on=True)
        for t in TOOLS:
            self.assertIn(t["name"], card)
        self.assertEqual(len(_mcp_tool_lines()), len(TOOLS))

    def test_mcp_off_falls_back_to_frames(self):
        card = _seat_card(_Srv(), mcp_on=False)
        self.assertIn("MCP off", card)
        self.assertNotIn("membrane_query", card)

    def test_writes_and_replay_follow_the_switches(self):
        card = _seat_card(_Srv(agent_writes=False, replay_sim=0.93), True)
        self.assertIn("report-only", card)
        self.assertIn("sim >= 0.93", card)

    def test_budget_none_is_uncapped_not_free(self):
        self.assertIn("uncapped", _seat_card(_Srv(router=_Router(None)), True))
        self.assertIn("free-tier only",
                      _seat_card(_Srv(router=_Router(0.0)), True))
        self.assertIn("$2.50/day",
                      _seat_card(_Srv(router=_Router(2.5)), True))


if __name__ == "__main__":
    unittest.main()
