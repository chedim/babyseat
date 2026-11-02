"""#858: a turn cut in the gate, and a normal turn after a restart, must both
be in reconstruct() on the chat's next turn. RED on main: (a) on_turn runs only
after the model returned, so a cut turn's message is never recorded; (b) a full
capture queue silently drops discourse (the person's message + reply)."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import test_busy_timeout_431 as B  # noqa: E402  (the gate harness)
from membrane import efield as EF  # noqa: E402
from membrane import entity_block as EB  # noqa: E402
from membrane.capture import Capturer, conversation  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402

CONV = "ses_858"
CALLS = {0: {"id": "call_1", "function": {"name": "bash", "arguments": '{"command": "ls"}'}}}


def _field():
    return EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")


class TestCutTurnIsRemembered(B._ProxyCase):
    def test_a_turn_cut_in_the_gate_is_in_reconstruct_next_turn(self):
        f = _field()
        cap = Capturer(f, mode="shadow", start=False)
        h = B._handler(f"http://127.0.0.1:{self.slow.server_address[1]}")
        h.server.capturer = cap
        h._forward_raw = mock.Mock(side_effect=AssertionError("a bare resend"))
        verdict = {"verdict": "escalate", "route": {"route": "local-a", "kind": "local"}}
        with conversation(CONV):
            h._v1_deliver(B._up("local-a", False), json.dumps({"bare": True}).encode(),
                          B.ASK, None, verdict, B.INJECTION)   # cut: seat_busy 503
            cap.flush()
        out = EB.reconstruct(f, conv=CONV) or ""
        self.assertIn(B.ASK, out, "the cut turn's message never reached the events log")


class TestNormalTurnAfterBacklog(unittest.TestCase):
    def test_a_turn_survives_a_backed_up_capture_queue(self):
        f = _field()
        cap = Capturer(f, mode="shadow", start=False, maxsize=2)
        with conversation(CONV):
            cap.on_calls("q0", CALLS)                      # tool traffic fills the queue
            cap.on_calls("q1", CALLS)
            cap.on_turn("restart then ask the backup question", "the answer is 2 GB")
            cap.flush()
        out = EB.reconstruct(f, conv=CONV) or ""
        self.assertIn("backup question", out, "discourse dropped silently when the queue was full")
