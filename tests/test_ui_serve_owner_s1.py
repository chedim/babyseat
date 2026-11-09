"""S1 (red first): /ui/serve's speaker is decided by the credential. A request
carrying the seat token is the operator, whatever X-Membrane-Speaker says; a
visitor without it is refused at the door (401)."""
import base64
import json
import os
import tempfile
import threading
import unittest

import pytest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from membrane import seatauth as SA
from membrane import server as NS
from membrane import settings as S
from tests.test_server import _CFG, _StubRunner, _store


@pytest.mark.tokenless      # the test sets each request's credential itself
class OwnerBySeatToken(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": cls.tmp.name, "MEMBRANE_STANDALONE": "1",
                                               "MEMBRANE_JWT": "0", "MEMBRANE_OPERATOR": "dima"})
        cls.env.start()
        cls.sessions = _store()
        h = NS.make_handler(hub=NS.SSEHub(), sessions=cls.sessions, messages=NS.MessageStore(),
                            config=_CFG, runner=_StubRunner())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), h)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        try:
            os.remove(S.path())
        except OSError:
            pass

    def _post(self, speaker, token):
        h = {"Content-Type": "application/json", "X-Membrane-Speaker": speaker}
        if token:
            h["Authorization"] = "Basic " + base64.b64encode(("opencode:" + SA.ensure()).encode()).decode()
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}/ui/serve/session", method="POST",
                                   data=b"{}", headers=h)
        with urllib.request.urlopen(r, timeout=10) as resp:
            return json.loads(resp.read())

    def test_seat_token_is_the_operator_whatever_the_header_says(self):
        s = self._post("Mallory", token=True)
        self.assertEqual(s["owner"], "dima")
        self.assertEqual(self._post("Eve", token=True)["id"], s["id"])    # #770: one chat

    def test_tokenless_visitor_is_refused_not_given_a_name(self):
        # the door (#842) wants the seat token on every :4096 write: a header name buys nothing
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("Bob", token=False)
        self.assertEqual(cm.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
