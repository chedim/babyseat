"""#770 (red first): one chat per person per seat. POST /session returns the
person's existing chat; a boot migration stamps the operator's chat and marks
their others readonly; posting to a readonly chat is refused with the person's
chat id; the latest-chat routes deliver to the owner's chat."""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
os.environ["MEMBRANE_JWT"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as NS  # noqa: E402
from membrane import ui_serve as UI  # noqa: E402
from membrane import watch_agents as WA  # noqa: E402

_CFG = {"provider": {"membrane": {"models": {"m": {"name": "m"}}}}}


class _Runner:
    def run(self, sid, text, user_info=None, user_parts=None):
        return "ran:" + sid


def _person(h):
    sub = h.headers.get("X-Test-Person")
    return types.SimpleNamespace(sub=sub, name=sub) if sub else None


class OneChat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        d = Path(tempfile.mkdtemp())
        cls.sessions = NS.SessionStore(str(d / "s.json"))
        h = NS.make_handler(hub=NS.SSEHub(), sessions=cls.sessions,
                            messages=NS.MessageStore(), config=_CFG, runner=_Runner())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), h)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _req(self, p, method="GET", body=None, who=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json"})
        if who:
            r.add_header("X-Test-Person", who)
        try:
            with mock.patch.object(UI, "token_person", _person):
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
        except OSError:                       # the server dropped the call: no refusal answered
            return 0, {}

    def test_same_person_gets_the_same_chat_another_gets_their_own(self):
        _, a1 = self._req("/session", "POST", {}, who="ann")
        _, a2 = self._req("/session", "POST", {}, who="ann")
        _, b1 = self._req("/session", "POST", {}, who="bob")
        self.assertEqual(a1["id"], a2["id"])
        self.assertNotEqual(a1["id"], b1["id"])
        self.assertEqual(b1["owner"], "bob")

    def test_a_loop_session_is_still_new_each_time(self):
        s = NS.SessionStore()
        a = s.create("watch: x")["id"]
        b = s.create("watch: x")["id"]
        self.assertNotEqual(a, b)

    def test_posting_to_a_readonly_chat_is_refused_with_the_persons_chat(self):
        _, mine = self._req("/session", "POST", {}, who="cat")
        old = self.sessions.create("old")
        self.sessions.sessions[old["id"]].update(owner="cat", readonly=True)
        code, out = self._req(f"/session/{old['id']}/message", "POST",
                              {"parts": [{"type": "text", "text": "hi"}]}, who="cat")
        self.assertGreaterEqual(code, 400)
        self.assertEqual(out.get("chat") or out.get("sessionID"), mine["id"])
        code, _ = self._req(f"/session/{old['id']}", who="cat")   # still readable
        self.assertEqual(code, 200)


class Migration(unittest.TestCase):
    def test_boot_stamps_the_operators_chat_and_marks_the_others_readonly(self):
        s = NS.SessionStore()
        keep, other = s.create("memo")["id"], s.create("older")["id"]
        s.sessions[other]["owner"] = "dmitrii"
        s.sessions[keep]["owner"] = ""
        NS.migrate_one_chat(s, keep, "dmitrii")
        self.assertEqual(s.sessions[keep]["owner"], "dmitrii")
        self.assertFalse(s.sessions[keep].get("readonly"))
        self.assertTrue(s.sessions[other].get("readonly"))
        NS.migrate_one_chat(s, keep, "dmitrii")                   # idempotent
        self.assertTrue(s.sessions[other].get("readonly"))
        self.assertFalse(s.sessions[keep].get("readonly"))


class Routes(unittest.TestCase):
    def test_an_escalation_for_a_goal_planted_by_p_lands_in_ps_chat(self):
        s = NS.SessionStore()
        pc, lc = s.create("p")["id"], s.create("newest")["id"]
        s.sessions[pc]["owner"] = "pat"
        s.sessions[lc]["owner"] = "other"
        s.sessions[lc]["time"]["updated"] += 99999                # the latest chat is not P's
        told = []
        srv = types.SimpleNamespace(_loop_runner=types.SimpleNamespace(sessions=s, messages=NS.MessageStore()),
                                    entity_store=types.SimpleNamespace(_srv=True, _resolve_spec=lambda s: None, docs={}))
        with mock.patch("membrane.decision_map._tell",
                        lambda est, p, ev, text, pool=None: told.append(p["chat"])):
            WA.escalate(srv, {"kind": "intent-planted", "owner": "pat"}, "e", "line")
        self.assertEqual(told, [pc])


if __name__ == "__main__":
    unittest.main()
