"""THE LIVE LANE (2026-10-08, red first): the dashboard streams the brain's
/event SSE through the proxy instead of polling.

The serve's /event is ONE broadcast of every session's events (the reason
v1 never proxied it). The proxy now relays it PER HA USER: a frame passes
only when it names a session the caller owns (or is the stream's own
`server.connected` hello); everything else -- other users' chats, the
watch loops' sessions, session-less internals -- never leaves HA.
"""
import asyncio
import json
import unittest

from ai_terminal import serve_proxy as SP
from ai_terminal.serve_proxy import (SSEFrames, allowed_path,
                                     event_session_id, visible_event)


def frame(type_, **props):
    return {"id": "evt_1", "type": type_, "properties": props}


class TestEventSessionId(unittest.TestCase):
    def test_top_level_session_id(self):
        self.assertEqual(event_session_id(frame(
            "message.part.delta", sessionID="s1", partID="p", delta="x")), "s1")
        self.assertEqual(event_session_id(frame(
            "session.status", sessionID="s2", status={"type": "busy"})), "s2")

    def test_nested_shapes(self):
        # session.deleted carries it under info (server.py hub_deleted)
        self.assertEqual(event_session_id(frame(
            "session.deleted", info={"sessionID": "s3"})), "s3")
        self.assertEqual(event_session_id(frame(
            "message.part.updated", part={"sessionID": "s4", "id": "p"})), "s4")
        self.assertEqual(event_session_id(frame(
            "message.updated", info={"sessionID": "s5", "id": "m"})), "s5")

    def test_none_when_absent(self):
        self.assertIsNone(event_session_id(frame("server.connected")))
        self.assertIsNone(event_session_id({"type": "x"}))
        self.assertIsNone(event_session_id("garbage"))


class TestVisibleEvent(unittest.TestCase):
    OWNERS = {"mine": "u1", "theirs": "u2"}

    def test_own_session_passes(self):
        self.assertTrue(visible_event(frame(
            "message.part.delta", sessionID="mine"), self.OWNERS, "u1"))

    def test_other_users_and_loops_blocked(self):
        for sid in ("theirs", "loop::watch: x", "ses_unknown"):
            self.assertFalse(visible_event(frame(
                "message.part.delta", sessionID=sid), self.OWNERS, "u1"), sid)

    def test_hello_passes_sessionless_blocked(self):
        self.assertTrue(visible_event(frame("server.connected"),
                                      self.OWNERS, "u1"))
        self.assertFalse(visible_event(frame("lsp.updated"),
                                       self.OWNERS, "u1"))

    def test_no_user_sees_nothing_but_hello(self):
        self.assertFalse(visible_event(frame(
            "session.idle", sessionID="mine"), self.OWNERS, ""))


class TestSSEFrames(unittest.TestCase):
    def test_split_across_chunks(self):
        p = SSEFrames()
        out = p.feed(b'data: {"type":"a","prop')
        self.assertEqual(out, [])
        out = p.feed(b'erties":{"sessionID":"s"}}\n\ndata: {"type":"b","properties":{}}\n\n')
        self.assertEqual([e["type"] for e in out], ["a", "b"])
        self.assertEqual(out[0]["properties"]["sessionID"], "s")

    def test_comments_and_crlf(self):
        p = SSEFrames()
        out = p.feed(b': ping\r\n\r\ndata: {"type":"c","properties":{}}\r\n\r\n')
        self.assertEqual([e["type"] for e in out], ["c"])

    def test_multiline_data_and_bad_json(self):
        p = SSEFrames()
        out = p.feed(b'data: {"type":\ndata: "d","properties":{}}\n\ndata: nope\n\n')
        self.assertEqual([e["type"] for e in out], ["d"])


class TestAllowList(unittest.TestCase):
    def test_agent_and_event_reachable(self):
        self.assertTrue(allowed_path("agent"))
        self.assertTrue(allowed_path("event"))
        self.assertFalse(allowed_path("global/event"))   # the unscoped twin


# ---- the stream handler, end to end with a fake upstream ----------------

class _Content:
    def __init__(self, chunks, owners):
        self._chunks, self._owners = chunks, owners

    async def iter_any(self):
        for c in self._chunks:
            if callable(c):          # a side effect mid-stream
                c(self._owners)
                continue
            yield c


class _Up:
    status = 200

    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Sess:
    closed = False

    def __init__(self, content):
        self._content = content
        self.calls = []

    def request(self, method, url, **k):
        self.calls.append((method, url))
        return _Up(self._content)


class _Recorder:
    """web.StreamResponse stand-in: records what reaches the browser."""
    last = None

    def __init__(self, *a, status=200, headers=None, **k):
        self.status = status
        self.headers = dict(headers or {})
        self.chunks = []
        _Recorder.last = self

    async def prepare(self, request):
        return self

    async def write(self, data):
        self.chunks.append(data)

    async def write_eof(self, *a):
        return None


class _User:
    id = "u1"
    name = "Dmitrii"


class _Req:
    method = "GET"
    query = {}

    def __init__(self, comp):
        self.app = {"hass": type("H", (), {"data": {"ai_terminal": comp}})()}
        self.match_info = {"path": "event"}

    def get(self, key, default=None):
        return _User() if key == "hass_user" else default


def _sse(ev):
    return b"data: " + json.dumps(ev).encode() + b"\n\n"


class TestStreamHandler(unittest.TestCase):
    def test_relays_only_the_callers_sessions(self):
        owners = {"mine": "u1", "theirs": "u2"}
        comp = {"serve_url": "http://brain:4096", "serve_user": "o",
                "serve_pass": "p", "owners": owners, "owners_store": None}
        chunks = [
            _sse(frame("server.connected")),
            _sse(frame("message.part.delta", sessionID="theirs", delta="secret")),
            _sse(frame("message.part.delta", sessionID="mine", delta="hel")),
            b": ping\n\n",
            # a chat created AFTER the stream opened becomes visible live
            lambda o: o.__setitem__("later", "u1"),
            _sse(frame("session.status", sessionID="later",
                       status={"type": "busy"})),
            _sse(frame("message.updated", info={"sessionID": "loop::w"})),
        ]
        sess = _Sess(_Content(chunks, owners))
        comp["http_session"] = sess
        orig = SP.web.StreamResponse
        SP.web.StreamResponse = _Recorder
        try:
            asyncio.run(SP.ServeProxyView().get(_Req(comp), path="event"))
        finally:
            SP.web.StreamResponse = orig
        rec = _Recorder.last
        self.assertEqual(rec.headers.get("Content-Type"), "text/event-stream")
        body = b"".join(rec.chunks).decode()
        self.assertNotIn("secret", body)
        self.assertNotIn("loop::w", body)
        got = [json.loads(line[6:])["type"] for line in body.splitlines()
               if line.startswith("data: ")]
        self.assertEqual(got, ["server.connected", "message.part.delta",
                               "session.status"])
        self.assertEqual(sess.calls, [("GET", "http://brain:4096/event")])


if __name__ == "__main__":
    unittest.main()
