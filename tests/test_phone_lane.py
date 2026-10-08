"""THE PHONE LANE (2026-10-08, the transcript review, red first).

A phone call laned `build`: a misheard sentence could reach the shell
executor. Pinned: agent 'phone' lanes 'phone' (reads, learn, intend --
never exec, entity writes or closes).

No live-HA tool: an `ha_state` reader was added and rolled back the same
day -- the seat models the boat itself (entities + getter scripts), the
engine does not hand it capabilities (the operator's call).
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import server as S  # noqa: E402

class TestLane(unittest.TestCase):
    def test_phone_is_its_own_lane(self):
        self.assertEqual(S._lane_of("phone"), "phone")
        tools = S._lane_tools("phone")
        for t in ("entity_read", "query", "learn", "intend", "state"):
            self.assertIn(t, tools)
        for t in ("entity_link", "entity_add", "entity_edit", "entity_remove",
                  "fulfill", "field_watch", "entity_inherit", "peer_post"):
            self.assertNotIn(t, tools)
        r = S._lane_refusal("entity_link", "phone")
        self.assertIn("phone", r)
        self.assertNotIn("plan mode", r)

    def test_no_capability_tool_rides_any_lane(self):
        for tools in (S.OPERATOR_TOOLS, S.LISTENER_TOOLS, S.PLAN_TOOLS, S.PHONE_TOOLS):
            self.assertNotIn("ha_state", tools)
        self.assertNotIn("ha_state", M.TOOL_IMPLS)

    def test_a_phone_turn_counts_as_a_person_speaking(self):
        self.assertNotEqual(S._lane_of("phone"), "listener")


if __name__ == "__main__":
    unittest.main()
