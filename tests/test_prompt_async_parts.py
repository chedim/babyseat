"""THE EMPTY BUBBLE (2026-10-08, red first): POST /prompt_async recorded the
user's row WITH its parts but started the turn as runner.run(sid, text,
user_info) -- no user_parts. run() announced message.updated at the door
with NO parts and marked the row announced, so no later announce ever
carried the text: a live client (the HA dashboard v2, the attached TUI)
showed an empty user bubble until it reloaded history from the store.

The contract: whatever lane accepts the message, the door's announcement
carries the user's text parts -- run() recovers them from the store when
its caller passed only the row's info.
"""
import os
import sys
import unittest
from pathlib import Path
from queue import Empty

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except Empty:
            return out


def _runner():
    hub = MS.SSEHub()
    r = MS.TurnRunner(hub, MS.SessionStore(), MS.MessageStore(), None, None,
                      system="", model="m")
    return r, hub


def _stored_row(r, sid, text):
    """The handler's _record_user shape: the row is stored with its part
    BEFORE the turn starts."""
    info = {"id": MS._gen_id("msg"), "sessionID": sid, "role": "user",
            "agent": "build", "time": {"created": 1}}
    parts = [{"id": MS._gen_id("prt"), "sessionID": sid,
              "messageID": info["id"], "type": "text", "text": text}]
    r.messages.append(sid, info, parts)
    return info, parts


class TestDoorCarriesParts(unittest.TestCase):
    def _announced_parts(self, r, hub, sid, info):
        q = hub.subscribe()
        _drain(q)
        # a busy session with a full queue: run() announces at the door and
        # returns the flood stub at once (no model call)
        guard = r._guard(sid)
        guard.acquire()
        r.queue_depth_max = 0
        try:
            res = r.run(sid, "ignored", info)          # the async lane's call
        finally:
            guard.release()
        self.assertEqual(res.get("status"), "busy")
        evs = _drain(q)
        ups = [e for e in evs if e.get("type") == "message.updated"
               and (e.get("info") or {}).get("id") == info["id"]]
        self.assertTrue(ups, "the row was never announced")
        return [e["part"] for e in evs if e.get("type") == "message.part.updated"
                and (e.get("part") or {}).get("messageID") == info["id"]]

    def test_info_only_caller_still_announces_the_text(self):
        r, hub = _runner()
        sid = r.sessions.create(title="chat")["id"]
        info, _ = _stored_row(r, sid, "[HA user: Dmitrii]\nqueued hello")
        parts = self._announced_parts(r, hub, sid, info)
        self.assertEqual([p.get("text") for p in parts],
                         ["[HA user: Dmitrii]\nqueued hello"])

    def test_explicit_parts_still_win(self):
        r, hub = _runner()
        sid = r.sessions.create(title="chat")["id"]
        info, parts = _stored_row(r, sid, "direct")
        q = hub.subscribe()
        _drain(q)
        guard = r._guard(sid)
        guard.acquire()
        r.queue_depth_max = 0
        try:
            r.run(sid, "direct", info, parts)
        finally:
            guard.release()
        got = [e["part"]["text"] for e in _drain(q)
               if e.get("type") == "message.part.updated"]
        self.assertEqual(got, ["direct"])


if __name__ == "__main__":
    unittest.main()
