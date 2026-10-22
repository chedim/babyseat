"""THE CONVERSATION KEY (#429, PR 2): membrane learns which chat a message
belongs to.

- the client's own session id when it sends one: the serve's loopback turn
  (`_turn_sid` in the body), opencode's headers (x-opencode-session-id,
  x-session-affinity, X-Session-Id), Claude Code's X-Claude-Code-Session-Id;
- else a stable hash of the transcript's first user message, so every turn of
  one chat maps to one key and two chats stay apart;
- else no key: the idle-gap session stays the last fallback.

Captured events carry the key (`conv` on the event record), so two
interleaved chats on one seat stay apart even inside one idle-gap session,
the case `capture.Sessions` merged.
"""
import io
import json
import os
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import capture as C  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from _ef_fakes import FakeEmbedder  # noqa: E402


def chat(*users, system="you are helpful"):
    msgs = [{"role": "system", "content": system}]
    for i, u in enumerate(users):
        msgs.append({"role": "user", "content": u})
        if i < len(users) - 1:
            msgs.append({"role": "assistant", "content": f"reply {i}"})
    return {"model": "m", "messages": msgs}


class TestConversationKey(unittest.TestCase):
    def test_serve_turn_sid_wins(self):
        body = dict(chat("hello"), _turn_sid="ses_serve1")
        self.assertEqual(C.conversation_key(body, {"x-session-affinity": "ses_other"}),
                         "sid:ses_serve1")

    def test_opencode_headers(self):
        for h in ("x-opencode-session-id", "x-session-affinity", "X-Session-Id",
                  "X-Claude-Code-Session-Id"):
            self.assertEqual(C.conversation_key(chat("hello"), {h: " ses_42 "}), "sid:ses_42", h)

    def test_header_lookup_is_case_insensitive_on_http_messages(self):
        from http.client import HTTPMessage
        m = HTTPMessage()
        m["X-Session-Affinity"] = "ses_7"
        self.assertEqual(C.conversation_key(chat("hello"), m), "sid:ses_7")

    def test_transcript_head_hash_is_stable_across_turns(self):
        k1 = C.conversation_key(chat("make the backup skip huge files"), {})
        k3 = C.conversation_key(chat("make the backup skip huge files", "and log it?",
                                     "thanks"), {})
        self.assertTrue(k1.startswith("head:"), k1)
        self.assertEqual(k1, k3)

    def test_two_chats_get_two_keys(self):
        self.assertNotEqual(C.conversation_key(chat("fix the router"), {}),
                            C.conversation_key(chat("plan the release"), {}))

    def test_head_reads_content_parts_and_ignores_whitespace(self):
        parts = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "fix   the router"}]}]}
        self.assertEqual(C.conversation_key(parts, {}),
                         C.conversation_key(chat("fix the router"), {}))

    def test_no_user_message_no_key(self):
        self.assertEqual(C.conversation_key({"messages": [{"role": "system", "content": "x"}]}, {}), "")
        self.assertEqual(C.conversation_key({}, None), "")

    def test_a_person_id_is_not_a_conversation(self):
        # OpenAI's `user` names the person, who may run many chats
        self.assertTrue(C.conversation_key(dict(chat("hi"), user="alice"), {}).startswith("head:"))


def _cap():
    f = EF.EntityField(MemBackend(), FakeEmbedder(), "seat@host")
    return C.Capturer(f, mode="shadow", start=False), f


def _user_events(f):
    return [d for _, d in f.b.scan("events") if d["role"] == "user"]


class TestCapturedEventsCarryTheKey(unittest.TestCase):
    def tearDown(self):
        C.set_conversation("")

    def test_two_interleaved_chats_stay_apart(self):
        c, f = _cap()
        a, b = (C.conversation_key(chat("fix the router"), {}),
                C.conversation_key(chat("plan the release"), {}))
        for key, text in ((a, "fix the router"), (b, "plan the release"),
                          (a, "yes"), (b, "yes"), (a, "now run the tests")):
            C.set_conversation(key)
            c.on_turn(text, f"ok: {text}")
        c.flush()
        evs = _user_events(f)
        by = {}
        for d in evs:
            by.setdefault(d.get("conv"), []).append(d["text"])
        self.assertEqual(sorted(by[a]), ["fix the router", "now run the tests", "yes"])
        self.assertEqual(sorted(by[b]), ["plan the release", "yes"])
        # one idle-gap session held both chats: the key is what splits them
        self.assertEqual(len({d["session"] for d in evs}), 1)

    def test_resent_turn_of_one_chat_still_collapses(self):
        c, f = _cap()
        C.set_conversation("sid:ses_1")
        for _ in range(3):
            c.on_turn("fix the router please")
        c.flush()
        self.assertEqual(len(_user_events(f)), 1)

    def test_calls_and_results_carry_the_key(self):
        c, f = _cap()
        C.set_conversation("sid:ses_1")
        calls = {0: {"id": "call_1", "function": {"name": "edit",
                                                  "arguments": '{"filePath": "a.py"}'}}}
        c.on_calls("q", calls)
        c.on_tool_result("call_1", "edit", '{"filePath": "a.py"}', "Edit applied.", "q")
        c.flush()
        obs = [d for _, d in f.b.scan("events") if d["stream"] == "observation"]
        self.assertEqual([d.get("conv") for d in obs], ["sid:ses_1"])

    def test_explicit_conv_overrides_the_thread(self):
        c, f = _cap()
        C.set_conversation("sid:ses_1")
        c.on_turn("hello from the panel", conv="sid:ses_9")
        c.flush()
        self.assertEqual(_user_events(f)[0].get("conv"), "sid:ses_9")

    def test_no_key_writes_no_field(self):
        c, f = _cap()
        C.set_conversation("")
        c.on_turn("hello there friend")
        c.flush()
        self.assertNotIn("conv", _user_events(f)[0])

    def test_a_serve_chat_names_itself_for_its_captures(self):
        c, f = _cap()
        C.set_conversation("sid:outer")
        with C.conversation("sid:ses_chat"):
            c.on_turn("look, a ball")
        self.assertEqual(C.current_conversation(), "sid:outer")
        c.flush()
        self.assertEqual(_user_events(f)[0].get("conv"), "sid:ses_chat")

    def test_the_key_is_per_thread(self):
        C.set_conversation("sid:main")
        seen = []
        t = threading.Thread(target=lambda: seen.append(C.current_conversation()))
        t.start()
        t.join()
        self.assertEqual(seen, [""])
        self.assertEqual(C.current_conversation(), "sid:main")


class TestTheProxyStampsTheKey(unittest.TestCase):
    """`_v1_chat` names the request's conversation before anything captures."""

    def tearDown(self):
        C.set_conversation("")

    def _run(self, body, headers):
        from membrane import lanes as L
        raw = json.dumps(body).encode()
        mem = types.SimpleNamespace(embedder=None)
        srv = types.SimpleNamespace(membrane=mem, model_follows_chat=False,
                                    default_seat_model="m", pending_embed_error="no embedder")
        h = L.MembraneOpenAIHandler.__new__(L.MembraneOpenAIHandler)
        h.server = srv
        hd = {"Content-Length": str(len(raw)), **headers}
        h.headers = hd
        h.rfile = io.BytesIO(raw)
        seen = []
        with mock.patch.object(L.MembraneOpenAIHandler, "_resolve_embedder",
                               lambda self, m: None), \
                mock.patch.object(L.MembraneOpenAIHandler, "_json_err_raw",
                                  lambda self, code, msg: seen.append(C.current_conversation())):
            h._v1_chat()
        return seen

    def test_session_header(self):
        self.assertEqual(self._run(chat("hi"), {"x-session-affinity": "ses_5"}), ["sid:ses_5"])

    def test_transcript_head(self):
        self.assertEqual(self._run(chat("plan the release", "and then?"), {}),
                         [C.conversation_key(chat("plan the release"), {})])

    def test_a_keyless_request_clears_the_last_one(self):
        C.set_conversation("sid:stale")       # a kept-alive connection's earlier request
        self.assertEqual(self._run({"model": "m", "messages": []}, {}), [""])


if __name__ == "__main__":
    unittest.main()
