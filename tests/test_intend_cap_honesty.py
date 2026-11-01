"""intend must be honest about the active-set cap (GOAL_SET_CAP)."""
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from membrane.intent import GoalSelection, IntentBoard, GOAL_SET_CAP
from tests.test_goal_intents import _store, _goal


class TestIntendIsHonestAboutTheCap(unittest.TestCase):
    def setUp(self):
        from membrane import opencode as OC
        self.est = _store()
        self.board = IntentBoard(Path(tempfile.mkdtemp()) / "intents.json")
        self.board.selection = GoalSelection(self.est)
        self.held = []
        for n in range(GOAL_SET_CAP):
            g = _goal(self.est, "held goal %d" % n, [1.0, 0.0, 0.0])
            self.board.selection.pin(g.eid, 5.0)
            self.held.append(g.eid)
        self.board.selection.refresh(time.time())

        class _Membrane:
            embedder = None
            store = None
            gate_notes = []

        srv = ThreadingHTTPServer(("127.0.0.1", 0), OC.MembraneOpenAIHandler)
        srv.membrane = _Membrane()
        srv.entity_store = self.est
        srv._intent_board = self.board
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.port = srv.server_address[1]
        self.addCleanup(srv.shutdown)

    def _intend(self, text):
        from membrane import mcp_server
        return mcp_server.tool_intend({"text": text}, self.port)

    def test_a_fourth_goal_is_not_called_pinned(self):
        reply = self._intend("a fourth goal that cannot fit")
        low = reply.lower()
        self.assertNotIn("pinned", low, reply)
        self.assertIn("not on the active set", low, reply)
        self.assertIn(str(GOAL_SET_CAP), reply, reply)
        self.assertIn("cap", low, reply)

    def test_an_existing_goal_off_the_set_says_so(self):
        _goal(self.est, "existing but off the set", [0.0, 1.0, 0.0])
        reply = self._intend("existing but off the set")
        low = reply.lower()
        self.assertNotIn("pinned (existing goal)", low, reply)
        self.assertIn("not on the active set", low, reply)

    def test_the_board_lists_open_goals_off_the_set_marked(self):
        _goal(self.est, "waiting off the set", [0.0, 1.0, 0.0])
        lines = self.board.report().splitlines()
        mine = [x for x in lines if "waiting off the set" in x]
        self.assertTrue(mine, lines)
        self.assertIn("off the set", mine[0].lower())
        for eid in self.held:
            hl = [x for x in lines if eid.rsplit(":", 1)[-1] in x]
            self.assertTrue(hl, lines)
            self.assertNotIn("off the set", hl[0].lower())


if __name__ == "__main__":
    unittest.main()
