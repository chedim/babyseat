"""THE ARGS THAT WERE SENT (2026-10-08, the ThinkPhone seat's audit).

~200 of the HA seat's tool calls were refused 'X required' for a value the
model DID send -- under the name it reaches for, not the schema's:
entity_read got eid/entity/name/key (or a wrapped {"args": "<json>"}),
intend got goal, peer_post got message, learn got text, query got
query/text, entities_related got q. The aliases resolve; the schema name
still wins. And the tool watchdog disarms on an ERRORING call too: each
refusal left its timer armed -> a false 'STILL PENDING ... ALL THREADS'
dump 45s later (~370 of them, most of a 6.4 MB log).
"""

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import mcp_server as M  # noqa: E402
from membrane import server as SRV  # noqa: E402


class _Http:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __enter__(self):
        self.orig = M._http
        M._http = lambda port, method, path, body=None: (
            self.calls.append((method, path, body)) or dict(self.reply))
        return self

    def __exit__(self, *a):
        M._http = self.orig


class TestArgHelper(unittest.TestCase):
    def test_schema_name_wins(self):
        self.assertEqual(M._arg({"spec": "a", "eid": "b"}, "spec", "eid"), "a")

    def test_alias_and_strip(self):
        self.assertEqual(M._arg({"eid": " w:agdroid "}, "spec", "eid"),
                         "w:agdroid")

    def test_wrapped_args_json(self):
        self.assertEqual(M._arg({"args": '{"eid": "m:goal:7bd4"}'},
                                "spec", "eid"), "m:goal:7bd4")

    def test_nothing(self):
        self.assertEqual(M._arg({"run": "true"}, "spec", "eid"), "")
        self.assertEqual(M._arg({"args": "not json"}, "spec"), "")


class TestToolsResolveAliases(unittest.TestCase):
    def test_entity_read_aliases(self):
        for args in ({"eid": "w:agdroid"}, {"entity": "w:agdroid"},
                     {"name": "w:agdroid"}, {"key": "w:agdroid"},
                     {"args": '{"eid": "w:agdroid"}'}):
            with _Http({"ok": False, "error": "x"}) as h:
                M.tool_entity_read(args, 0)
            self.assertIn("spec=w%3Aagdroid", h.calls[0][1], args)

    def test_entity_read_run_rides_with_alias(self):
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_entity_read({"name": "Executor", "run": "true"}, 0)
        self.assertIn("spec=Executor&run=1", h.calls[0][1])

    def test_entity_edit_eid(self):
        """The 09:01 exhibit: both goal-closing edits sent eid."""
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_entity_edit({"eid": "ca669752948d", "summary": "LANDED"}, 0)
        self.assertEqual(h.calls[0][2]["spec"], "ca669752948d")

    def test_intend_goal(self):
        with _Http({"ok": False, "error": "x"}) as h:
            M.tool_intend({"goal": "wire the seat into HA"}, 0)
        self.assertEqual(h.calls[0][2]["text"], "wire the seat into HA")

    def test_peer_post_message(self):
        with _Http({"ok": True}) as h:
            M.tool_peer_post({"message": "hello"}, 0)
        self.assertEqual(h.calls[0][2]["text"], "hello")

    def test_learn_text(self):
        with _Http({"written": False}) as h:
            M.tool_learn({"text": "adb cannot link"}, 0)
        self.assertEqual(h.calls[0][2]["lesson"], "adb cannot link")

    def test_related_q(self):
        with _Http({"related": []}) as h:
            M.tool_related_entities({"q": "mqtt"}, 0)
        self.assertIn("q=mqtt", h.calls[0][1])

    def test_missing_still_refused(self):
        with self.assertRaises(M.ProxyError):
            M.tool_peer_post({}, 0)
        with self.assertRaises(M.ProxyError):
            M.tool_learn({"lesson": "  "}, 0)


class TestWatchdogDisarmsOnError(unittest.TestCase):
    def test_erroring_tool_leaves_no_armed_timer(self):
        hub = SRV.SSEHub()
        msgs = SRV.MessageStore()
        sessions = SRV.SessionStore()
        replies = iter([
            {"choices": [{"message": {"role": "assistant", "content": "",
                                      "tool_calls": [{
                                          "id": "call_1", "type": "function",
                                          "function": {"name": "learn",
                                                       "arguments": "{}"}}]},
                          "finish_reason": "tool_calls"}]},
            {"choices": [{"message": {"role": "assistant",
                                      "content": "done"},
                          "finish_reason": "stop"}]},
        ])

        def boom(name, args, call_id=""):
            raise M.ProxyError(400, "lesson required")
        runner = SRV.TurnRunner(hub, sessions, msgs,
                                lambda body: next(replies), boom,
                                system="", model="m", tools=[])
        runner.srv = None
        runner.TOOL_DUMP_S = 0.05
        sid = sessions.create()["id"]
        runner.run(sid, "go")
        self.assertEqual(getattr(runner, "_tool_t0", {}), {},
                         "the errored call's timer was left armed")


if __name__ == "__main__":
    unittest.main()
