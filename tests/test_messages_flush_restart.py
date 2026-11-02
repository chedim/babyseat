"""A REPLY'S LAST WORDS SURVIVE A RESTART (the operator, 2026-11-02: "restart dropped some chat
history"). A new message is written at once, but a change to one (its text, a tool part) waits
for the FLUSH_MIN_S cadence, and nothing wrote the last change after it: a restart lost the
end of a reply. The store now writes a skipped change once the cadence allows, close() writes
what is pending, and the seat's teardown closes the chat stores before anything that can hang.
"""
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.server import MessageStore  # noqa: E402

SID = "ses_alice"


def _info(text_id="m1"):
    return {"id": text_id, "sessionID": SID, "role": "assistant",
            "time": {"created": int(time.time() * 1000)}}


def _text(t):
    return [{"type": "text", "text": t}]


def _reloaded(path):
    return [p.get("text") for r in MessageStore(path).list(SID) for p in r["parts"]]


class LastWordsSurvive(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "serve-msgs.json")

    def test_a_change_inside_the_cadence_is_written_soon_after(self):
        s = MessageStore(self.path)
        s.FLUSH_MIN_S = 0.2
        s.append(SID, _info(), _text("first words"))
        s.append(SID, _info(), _text("first words, and the end"))      # inside the cadence
        time.sleep(0.6)
        self.assertEqual(_reloaded(self.path), ["first words, and the end"])

    def test_close_writes_what_is_pending(self):
        s = MessageStore(self.path)
        s.append(SID, _info(), _text("first words"))
        s.append(SID, _info(), _text("first words, and the end"))
        s.close()
        self.assertEqual(_reloaded(self.path), ["first words, and the end"])

    def test_nothing_pending_writes_nothing_more(self):
        s = MessageStore(self.path)
        s.FLUSH_MIN_S = 0.2
        s.append(SID, _info(), _text("only"))
        time.sleep(0.4)
        s.close()
        self.assertEqual(_reloaded(self.path), ["only"])


class TheTeardownClosesTheChatFirst(unittest.TestCase):
    def test_chat_stores_close_before_the_lease_starts(self):
        src = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()
        fin = src[src.index("# THE TEARDOWN'S BOUND"):]
        closes = re.search(r"\.messages\.close\(\)", fin)
        lease = fin.index("_teardown_halt.start()")
        self.assertIsNotNone(closes, "the teardown never closes the message store")
        self.assertLess(closes.start(), lease, "the chat must be written before anything can hang")
        self.assertRegex(fin[:lease], r"\.sessions\.flush\(\)")


if __name__ == "__main__":
    unittest.main()
