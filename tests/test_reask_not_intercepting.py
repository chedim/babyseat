"""The pronoun re-ask no longer intercepts (operator 2026-11-11): '"it": a or b?' is still
shown in the chat, but the model's turn runs anyway, told what "it" may mean. A later change
lets the model ask instead of membrane."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from membrane import convgraph as CG  # noqa: E402
from test_conversation_graph import SID, _seat, _turn  # noqa: E402


class ReaskDoesNotIntercept(unittest.TestCase):
    def test_the_model_is_told_the_readings(self):
        srv, runner = _seat()
        _turn(srv, runner, 1, "look", "I changed membrane/a.py and membrane/b.py.")
        runner.messages.add(SID, "msg_u2", "user", "[user: Dima]\ndelete it")
        out = CG.before_turn(srv, runner, SID, {"id": "msg_u2"}, "[user: Dima]\ndelete it")
        self.assertTrue(out and out.get("reask"))          # still shown
        note = CG.take_note(srv, SID)
        self.assertIsNotNone(note)
        self.assertIn("membrane/a.py", note)
        self.assertIn("membrane/b.py", note)

    def test_the_seat_does_not_return_before_the_turn(self):
        src = (Path(__file__).parent.parent / "membrane" / "server.py").read_text()
        self.assertNotIn('"reask": _ref["reask"]}', src)


if __name__ == "__main__":
    unittest.main()
