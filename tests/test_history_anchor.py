"""THE ANCHOR (2026-10-11, "why is auto-chat not auto-chatting?"): the
predict role's continuations add only Argus's replies; once 12 of them
filled the replay window it held no user message, and the backend
answered with nothing (done_reason 'load') every round. The last user
message always rides first."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as SV  # noqa: E402
from membrane.server import MessageStore, SessionStore, SSEHub, TurnRunner  # noqa: E402


def _row(i, role, text):
    return ({"id": f"msg_{i}", "role": role, "time": {"created": i}},
            [{"id": f"prt_{i}", "type": "text", "text": text}])


class TestAnchor(unittest.TestCase):
    def _runner(self, rows):
        d = tempfile.mkdtemp()
        ss = SessionStore(str(Path(d) / "s.json"))
        ms = MessageStore(str(Path(d) / "m.json"))
        sid = "ses_t"
        for i, (role, text) in enumerate(rows):
            info, parts = _row(i, role, text)
            info["sessionID"] = sid
            ms.append(sid, info, parts)
        return TurnRunner(SSEHub(), ss, ms, None, None, system="", model="glm"), sid

    def test_an_all_assistant_window_keeps_the_last_user_message(self):
        rows = [("user", "keep an eye on the battery")] + \
               [("assistant", f"reply {i}") for i in range(SV.HISTORY_MAX_MESSAGES + 5)]
        r, sid = self._runner(rows)
        h = r._prior_history(sid, "", anchor=True)
        self.assertEqual(len(h), SV.HISTORY_MAX_MESSAGES)
        self.assertEqual(h[0], {"role": "user", "content": "keep an eye on the battery"})
        self.assertEqual(h[-1]["content"], f"reply {SV.HISTORY_MAX_MESSAGES + 4}")

    def test_no_user_ever_gets_a_plain_one(self):
        r, sid = self._runner([("assistant", f"r{i}") for i in range(3)])
        self.assertEqual(r._prior_history(sid, "", anchor=True)[0]["role"], "user")

    def test_a_normal_turn_brings_its_own_user_message(self):
        r, sid = self._runner([("assistant", f"r{i}") for i in range(3)])
        self.assertEqual([m["role"] for m in r._prior_history(sid, "")], ["assistant"] * 3)

    def test_a_window_with_a_user_message_is_unchanged(self):
        rows = [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")]
        r, sid = self._runner(rows)
        self.assertEqual([m["content"] for m in r._prior_history(sid, "", anchor=True)], ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
