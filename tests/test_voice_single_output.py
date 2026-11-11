"""One reply, one spoken output (red test for the duplicate-voice bug).

A reply in a speaker session is spoken sentence by sentence while it streams
(speaker.py, voice_io.on_edit(stream=True)); the same reply is also written to
voice:out `say` by the chat path (chat_io.py / lanes.py, on_edit non-stream).
The SAY_GAP_S guard only covers a quick second line, so once the stream takes
longer than the gap the reply is spoken twice."""
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import voice_io as VIO  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402

WHO = "Dmitrii Chechetkin"
REPLY = "The battery is at 63 percent."


class TestSingleSpokenOutput(unittest.TestCase):
    def test_one_reply_is_spoken_once(self):
        srv = types.SimpleNamespace(entity_store=EntityStore(path=None, shared=False))
        out = VIO.entity(srv, "out", WHO)
        out.properties["on"] = True
        # path 1: the speaker lane speaks the reply as it streams
        out.properties["say"] = REPLY
        VIO.on_edit(srv, out, stream=True)
        # the stream took longer than SAY_GAP_S before the turn finished
        out.properties["said_ts"] -= VIO.SAY_GAP_S + 5
        # path 2: the chat lane's voice:out write of the same reply
        out.properties["say"] = REPLY
        VIO.on_edit(srv, out)
        spoken = [x["text"] for x in out.properties.get("said", [])]
        self.assertEqual(spoken, [REPLY], f"spoken {len(spoken)}x: {spoken}")


if __name__ == "__main__":
    unittest.main()
