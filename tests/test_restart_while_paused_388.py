"""#388: a restart is allowed while the seat is paused.

The pause's write gate (lanes._paused_write_refused) stood in front of the whole
/membrane/method/* route, so `presence.restart` answered 423 while paused and a
staged deploy waited for a resume. Pinned here:
  - POST /membrane/method/call presence.restart goes through while paused;
  - every other method call/define still answers 423 while paused;
  - nothing about the pause is written to disk; the restart's reply says the
    seat comes back paused (#395 changed the spec: the person's pause is
    carried through the re-exec's environment, tests/test_pause_carry_395.py).
"""
import json
import os
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

from membrane import lanes as L  # noqa: E402
from membrane import restarts as RS  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from tests.test_jwt_auth import _jwt_env  # noqa: E402
from tests.test_offbox_auth import _req  # noqa: E402

CALL = "/membrane/method/call"
DEFINE = "/membrane/method/define"


def _seat(srv, d):
    """The :11500 handler's server, dressed as a seat (the method route's needs)."""
    srv.backend = "http://127.0.0.1:9"
    srv.data_dir = d
    srv.entity_store = EntityStore(path=None, shared=False)
    srv.entity_store._srv = srv
    srv.paused = False
    srv.membrane = types.SimpleNamespace(audit_path=str(Path(d) / "audit.jsonl"))
    return srv


class TestRestartWhilePaused(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = _jwt_env(MEMBRANE_SEAT_TOKEN_FILE=os.path.join(self.tmp.name, "seat-token"),
                            MEMBRANE_DATA_DIR=self.tmp.name)
        self.env.start()
        os.environ.pop("MEMBRANE_NERVE_PAUSE", None)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), L.MembraneOpenAIHandler)
        self.srv = _seat(self.httpd, self.tmp.name)
        RS.bind(self.srv)                                   # presence implements restart
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        RS._SEAT["srv"] = None
        self.env.stop()
        self.tmp.cleanup()

    def _post(self, path, body):
        code, b, _ = _req(self.httpd.server_address[1], path, "POST", body)
        try:
            return code, json.loads(b or b"{}")
        except ValueError:
            return code, b

    def test_presence_restart_goes_through_while_paused(self):
        self.srv.paused = True
        with mock.patch.object(RS, "do_restart",
                               return_value={"rc": 0, "text": "restarting"}) as dr:
            code, out = self._post(CALL, {"object": "presence", "method": "restart",
                                          "args": {"why": "new code staged"}})
        self.assertEqual(code, 200, out)
        dr.assert_called_once()
        self.assertEqual(dr.call_args[0][1], "new code staged")
        self.assertTrue(self.srv.paused)                    # the restart itself does not resume

    def test_other_methods_stay_refused_while_paused(self):
        self.srv.paused = True
        code, out = self._post(CALL, {"object": "presence", "method": "elsewise", "args": {}})
        self.assertEqual(code, 423, out)
        code, out = self._post(DEFINE, {"on": "presence", "method": "restart",
                                        "command": "echo hi"})
        self.assertEqual(code, 423, out)

    def test_the_reply_says_the_seat_comes_back_paused(self):
        self.srv.paused = True
        before = set(os.listdir(self.tmp.name))
        with mock.patch.object(RS.threading, "Thread"), mock.patch("membrane.seat_panel.note"):
            out = RS.do_restart(self.srv, "new code", delay_s=0)
        self.assertEqual(out["rc"], 0)
        self.assertIn("comes back paused", out["text"])         # #395 (was "unpaused", #388)
        new = set(os.listdir(self.tmp.name)) - before
        for name in new:                                    # only the restart's own reason file
            self.assertNotIn("paus", Path(self.tmp.name, name).read_text().lower(), name)
        RS.RESTART["requested"] = False

    def test_a_running_seat_restart_reply_is_unchanged(self):
        with mock.patch.object(RS.threading, "Thread"), mock.patch("membrane.seat_panel.note"):
            out = RS.do_restart(self.srv, "new code", delay_s=0)
        self.assertNotIn("paused", out["text"])
        RS.RESTART["requested"] = False


if __name__ == "__main__":
    unittest.main()
