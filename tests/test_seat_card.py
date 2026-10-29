"""The seat card (opencode._seat_card): this session's switches rendered
for the model -- mode-specific, tool list from the MCP registry, never
claiming a state the seat is not in."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane.opencode import _seat_card, _mcp_tool_lines
from membrane.mcp_server import TOOLS
from membrane.toolset import RETIRED


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
            # issue #24: a retired tool does not render -- the diet
            # (2026-10-09) took it off the model's surface, the card
            # had kept inviting it
            if t["name"] in RETIRED:
                self.assertNotIn(t["name"], card)
            else:
                self.assertIn(t["name"], card)
        self.assertEqual(len(_mcp_tool_lines()),
                         len(TOOLS) - len([t for t in TOOLS
                                           if t["name"] in RETIRED]))

    def test_mcp_off_falls_back_to_frames(self):
        card = _seat_card(_Srv(), mcp_on=False)
        self.assertIn("MCP off", card)
        self.assertNotIn("query", card)

    def test_writes_and_replay_follow_the_switches(self):
        card = _seat_card(_Srv(agent_writes=False), True)
        self.assertIn("report-only", card)
        self.assertIn("REPLAY A/B: off (shadow)", card)
        served = _seat_card(_Srv(replay_ab=0.3), True)
        self.assertIn("REPLAY A/B: replayable asks", served)
        self.assertIn("30.0% served as pure replay", served)
        self.assertIn("you are never bypassed", served)

    def test_budget_none_is_uncapped_not_free(self):
        self.assertIn("uncapped", _seat_card(_Srv(router=_Router(None)), True))
        self.assertIn("free-tier only",
                      _seat_card(_Srv(router=_Router(0.0)), True))
        self.assertIn("$2.50/day",
                      _seat_card(_Srv(router=_Router(2.5)), True))


class _Parsed(Exception):
    pass


def _parse(argv, env):
    """Run opencode.main's REAL parser on argv under env, stop right after
    parse_args (no server boots)."""
    import argparse
    import os
    import sys
    from unittest import mock
    from membrane import opencode
    real = argparse.ArgumentParser.parse_args
    got = {}

    def grab(self, *a, **k):
        got["ns"] = real(self, *a, **k)
        raise _Parsed()
    with mock.patch.object(argparse.ArgumentParser, "parse_args", grab), \
            mock.patch.object(sys, "argv", ["membrane-opencode"] + argv), \
            mock.patch.dict(os.environ, env, clear=False):
        os.environ.pop("MEMBRANE_NO_CONTEXT", None) if "MEMBRANE_NO_CONTEXT" \
            not in env else None
        try:
            opencode.main()
        except _Parsed:
            pass
    return got["ns"]


class TestNoContextEnvDefault(unittest.TestCase):
    """The stateless seat is the default (operator, 2026-10-29: the field is
    the memory, the transcript is not replayed); MEMBRANE_NO_CONTEXT=0 or
    --with-context keeps it; the flags still win both ways."""

    def test_on_by_default(self):
        self.assertTrue(_parse([], {}).no_context)

    def test_with_context_turns_it_off(self):
        self.assertFalse(_parse(["--with-context"], {}).no_context)

    def test_env_zero_turns_it_off(self):
        self.assertFalse(_parse([], {"MEMBRANE_NO_CONTEXT": "0"}).no_context)

    def test_env_one_stays_on(self):
        self.assertTrue(_parse([], {"MEMBRANE_NO_CONTEXT": "1"}).no_context)

    def test_no_context_overrides_env_zero(self):
        self.assertTrue(_parse(["--no-context"],
                               {"MEMBRANE_NO_CONTEXT": "0"}).no_context)


if __name__ == "__main__":
    unittest.main()


class TestEntityCard(unittest.TestCase):
    def _srv(self, **kw):
        from membrane.efield import EntityField
        from membrane.efstore import MemBackend
        from _ef_fakes import FakeEmbedder

        class _Cap:
            mode = "on"
        return _Srv(efield=EntityField(MemBackend(), FakeEmbedder(), "o"), capturer=_Cap(), **kw)

    def test_the_field_is_described_only_when_on(self):
        self.assertNotIn("ENTITY FIELD", _seat_card(_Srv(), mcp_on=False))
        card = _seat_card(self._srv(), mcp_on=False)
        self.assertIn("SINCE YOUR LAST MESSAGE", card)
        self.assertIn("'#N' is an EVENT number", card)
        nc = _seat_card(self._srv(no_context=True), mcp_on=False)
        self.assertIn("THE CONVERSATION SO FAR", nc)
        self.assertNotIn("SINCE YOUR LAST MESSAGE", nc)
