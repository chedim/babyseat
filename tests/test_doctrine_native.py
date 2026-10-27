"""THE DOCTRINE'S NATIVE-SERVE CONTRACT (stage 3 follow-up): the model must
be TAUGHT its surface and its tools reality wherever it connects -- through
the opencode TUI, or through membrane's own serve REST/SSE server (opencode
replaced; a phone companion or a script is a first-class client, no editor
in the world). Pins:

  - the STATIC doctrine (_RAG_SYSTEM) is client-neutral: it never claims
    the editor; it names the run lane (run -> a command entity)
    and the zero-builtin default;
  - it still teaches the frames and the lane discipline (the parts that
    earned their keep unchanged);
  - the SEAT CARD gains a SURFACE line when the seat serves (and none
    when it does not).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.rag import (_DOCTRINE_STAMP, doctrine_instructions_text)
from membrane.opencode import _seat_card


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


class TestDoctrineNative(unittest.TestCase):
    def setUp(self):
        self.text = doctrine_instructions_text()

    def test_stamp_is_the_first_line(self):
        self.assertEqual(self.text.splitlines()[0], _DOCTRINE_STAMP)

    def test_client_neutral_no_editor_claim(self):
        # serve mode answers phones and scripts; the doctrine must not tell
        # the model it LIVES in the editor (the old "between the opencode
        # editor and the model server" world)
        self.assertNotIn("between the opencode editor", self.text)
        self.assertIn("REMOTE clients", self.text)

    def test_doctrine_names_the_run_lane(self):
        self.assertIn("exec(action='run', code=", self.text)   # #614: code runs as a tree in the field
        self.assertIn("is a METHOD", self.text)                 # #576 part F: what repeats is a method

    def test_doctrine_says_builtin_free_by_default(self):
        self.assertIn("NO built-in", self.text)
        # the ablation is the escape hatch, named as such
        self.assertIn("MEMBRANE_BUILTIN_TOOLS", self.text)

    def test_doctrine_still_teaches_the_frames(self):
        self.assertIn("membrane: learn that", self.text)
        self.assertIn("membrane: forget #N", self.text)
        self.assertIn("At most ONE canonical frame", self.text)

    def test_seat_card_serve_line_follows_the_switch(self):
        card = _seat_card(_Srv(), True, serve=True)
        self.assertIn("SURFACE", card)
        self.assertIn("REMOTE", card)
        self.assertNotIn("SURFACE", _seat_card(_Srv(), True))

    def test_tools_takeover_line_lives_in_both_layers(self):
        # the card's EXEC paragraph (session-conditional) and the doctrine's
        # static bullet both teach the run lane, so the model hears it twice
        card = _seat_card(_Srv(), True)
        self.assertIn("run", card)
        self.assertIn("run", self.text)


if __name__ == "__main__":
    unittest.main()