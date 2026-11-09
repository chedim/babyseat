"""#966 Part A (red first): owner, MEMBRANE_OPERATOR and OPERATOR_SUBS are ONE person.
Migration and the chat pick never choose a newer empty chat over the real one,
read-only leftovers are not choices, the choice holds on later cold starts with
no MEMBRANE_OPERATOR_CHAT, and other people's chats are left alone. Requests
carry the page's loopback cookie, not the internal credential."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as NS  # noqa: E402
from membrane import seatauth as SA  # noqa: E402
from membrane import ui_serve as UI  # noqa: E402

_CFG = {"provider": {"membrane": {"models": {"m": {"name": "m"}}}}}


class _Runner:
    def run(self, sid, text, user_info=None, user_parts=None):
        return "ran:" + sid


class OwnerIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "MEMBRANE_JWT": "0", "MEMBRANE_SEAT_TOKEN_FILE": os.path.join(self.tmp.name, "seat-token"),
            "MEMBRANE_DATA_DIR": self.tmp.name, "MEMBRANE_OPERATOR": "alice"})
        self.env.start()
        os.environ.pop("MEMBRANE_OPERATOR_CHAT", None)
        self.tok = SA.ensure()
        NS.OPERATOR_SUBS.clear()
        self.sessions = NS.SessionStore(os.path.join(self.tmp.name, "s.json"))
        self.messages = NS.MessageStore()
        h = NS.make_handler(hub=NS.SSEHub(), sessions=self.sessions,
                            messages=self.messages, config=_CFG, runner=_Runner())
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        NS.OPERATOR_SUBS.clear()
        self.env.stop()
        self.tmp.cleanup()

    def _post(self):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/session", method="POST",
                                   data=b"{}", headers={"Content-Type": "application/json",
                                                        "Cookie": f"{UI._JWT.COOKIE}={self.tok}"})
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    def _chat(self, owner, said=0, readonly=False, age=0):
        s = self.sessions.create("c")
        row = self.sessions.sessions[s["id"]]
        row["owner"] = owner
        row["time"]["created"] = row["time"]["updated"] = 1_000_000 + age
        if readonly:
            row["readonly"] = True
        for i in range(said):
            self.messages.append(s["id"], {"id": f"msg_{s['id']}_{i}", "role": "user"},
                                 [{"type": "text", "text": "hi"}])
        return s["id"]

    def test_pick_never_prefers_a_newer_empty_chat_over_the_real_one(self):
        real = self._chat("owner", said=3, age=0)
        self._chat("alice", said=0, age=50)          # newer, empty, same person
        self.assertEqual(self._post()["id"], real)

    def test_readonly_leftovers_are_not_choices(self):
        real = self._chat("alice", said=2, age=0)
        self._chat("alice", said=5, readonly=True, age=90)
        self.assertEqual(self._post()["id"], real)

    def test_migration_unifies_owner_operator_and_subs_without_env_chat(self):
        NS.OPERATOR_SUBS.add("sub-1")
        real = self._chat("sub-1", said=4, age=0)
        e1 = self._chat("owner", said=0, age=10)
        e2 = self._chat("alice", said=0, age=20)
        bob = self._chat("bob", said=0, age=30)
        NS.boot_migrate_one_chat(self.sessions, env={"MEMBRANE_OPERATOR": "alice"}, messages=self.messages)
        rows = self.sessions.sessions
        self.assertFalse(rows[real].get("readonly"))
        self.assertEqual(rows[real]["owner"], "alice")
        self.assertTrue(rows[e1].get("readonly") and rows[e2].get("readonly"))
        self.assertFalse(rows[bob].get("readonly"))
        self.assertEqual(rows[bob]["owner"], "bob")

    def test_migration_holds_on_a_later_cold_start(self):
        real = self._chat("owner", said=2, age=0)
        self._chat("alice", said=0, age=40)
        env = {"MEMBRANE_OPERATOR": "alice"}
        NS.boot_migrate_one_chat(self.sessions, env=env, messages=self.messages)
        self._chat("alice", said=0, age=80)           # a new empty chat appears between boots
        NS.boot_migrate_one_chat(self.sessions, env=env, messages=self.messages)
        live = [sid for sid, s in self.sessions.sessions.items()
                if NS._is_op(s.get("owner")) and not s.get("readonly")]
        self.assertEqual(live, [real])


if __name__ == "__main__":
    unittest.main()
