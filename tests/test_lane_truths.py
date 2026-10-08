"""THE LANES' TRUTHS (2026-10-08, the transcript review, red first).

  * doctrine teaches "COMPOSITION IS INHERITANCE: entity_inherit" -- the
    tool existed in NO lane: 52 refusals, goals needing ISA edges stalled;
  * a listener calling `intend` was told "plan mode: ..." (11x) and
    concluded "this seat is in plan mode";
  * peer_post answered "Post failed: unknown error" 31 of 31 times (the
    seat has no peer channel) -- the props of an imagined sister seat.
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


class TestInherit(unittest.TestCase):
    def test_entity_inherit_is_a_build_hand(self):
        self.assertIn("entity_inherit", S.OPERATOR_TOOLS)
        self.assertIn("entity_inherit", S.LISTENER_TOOLS)
        self.assertNotIn("entity_inherit", S.PLAN_TOOLS)


class TestRefusalTexts(unittest.TestCase):
    def test_plan_texts_only_in_plan(self):
        r = S._lane_refusal("intend", "listener")
        self.assertIsNotNone(r)
        self.assertNotIn("plan mode", r)
        self.assertIn("listener", r)
        self.assertIn("plan mode", S._lane_refusal("intend", "plan"))


class _Http:
    def __init__(self, reply):
        self.reply = reply

    def __enter__(self):
        self.orig = M._http
        M._http = lambda port, method, path, body=None: dict(self.reply)
        return self

    def __exit__(self, *a):
        M._http = self.orig


class TestPeerPost(unittest.TestCase):
    def test_no_channel_says_so(self):
        with _Http({"ok": False, "error": "no peer channel on this seat"}):
            with self.assertRaises(M.ProxyError) as cm:
                M.tool_peer_post({"text": "hi"}, 0)
        self.assertIn("no peer channel", str(cm.exception))

    def test_route_names_the_reason(self):
        from membrane import peers as P
        p = P.PeerSubstrate.__new__(P.PeerSubstrate)
        p._chan = None
        self.assertIn("no peer channel", P.post_failure_reason(p))


if __name__ == "__main__":
    unittest.main()
