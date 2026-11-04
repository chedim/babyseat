"""#842 (red first): no tokenless requests. A loopback caller with no credential
is refused (401); the seat token, the UI cookie (operator = MEMBRANE_OPERATOR,
else owner) and a JWT (its sub) are accepted; the page served to loopback sets
the pre-minted HttpOnly cookie."""
import json
import os
import sys
import pytest
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as NS  # noqa: E402
from membrane import seatauth as SA  # noqa: E402
from membrane import ui_serve as UI  # noqa: E402

pytestmark = pytest.mark.tokenless

_CFG = {"provider": {"membrane": {"models": {"m": {"name": "m"}}}}}


class _Runner:
    def run(self, sid, text, user_info=None, user_parts=None):
        return "ran:" + sid


class NoTokenless(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {
            "MEMBRANE_JWT": "0", "MEMBRANE_SEAT_TOKEN_FILE": os.path.join(self.tmp.name, "seat-token"),
            "MEMBRANE_DATA_DIR": self.tmp.name, "MEMBRANE_OPERATOR": "alice"})
        self.env.start()
        self.tok = SA.ensure()
        self.sessions = NS.SessionStore(os.path.join(self.tmp.name, "s.json"))
        h = NS.make_handler(hub=NS.SSEHub(), sessions=self.sessions,
                            messages=NS.MessageStore(), config=_CFG, runner=_Runner())
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), h)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.env.stop()
        self.tmp.cleanup()

    def _req(self, path, method="GET", body=None, headers=None):
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                   data=json.dumps(body).encode() if body is not None else None,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw), resp.headers
                except ValueError:
                    return resp.status, raw, resp.headers
        except urllib.error.HTTPError as e:
            return e.code, {}, e.headers

    def test_tokenless_loopback_post_session_is_401(self):
        code, _, _ = self._req("/session", "POST", {})
        self.assertEqual(code, 401)

    def test_seat_token_is_the_operator(self):
        code, s, _ = self._req("/session", "POST", {}, {"Authorization": "Bearer " + self.tok})
        self.assertEqual(code, 200)
        self.assertEqual(s["owner"], "alice")
        _, s2, _ = self._req("/session", "POST", {}, {"Authorization": "Bearer " + self.tok})
        self.assertEqual(s["id"], s2["id"])

    def test_ui_cookie_is_the_operator(self):
        code, s, _ = self._req("/session", "POST", {}, {"Cookie": f"{UI._JWT.COOKIE}={self.tok}"})
        self.assertEqual(code, 200)
        self.assertEqual(s["owner"], "alice")

    def test_operator_falls_back_to_owner(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("MEMBRANE_OPERATOR", None)
            _, s, _ = self._req("/session", "POST", {}, {"Authorization": "Bearer " + self.tok})
        self.assertEqual(s["owner"], NS.OWNER_SUB)

    def test_jwt_is_its_sub(self):
        who = types.SimpleNamespace(sub="alice", name="alice")
        with mock.patch.object(UI, "token_person", lambda h: who):
            code, s, _ = self._req("/session", "POST", {}, {"Authorization": "Bearer jwt"})
        self.assertEqual((code, s.get("owner")), (200, "alice"))

    def test_served_page_sets_the_premined_httponly_cookie(self):
        with mock.patch.object(UI, "enabled", lambda: True):
            code, _, hdr = self._req("/")
        self.assertEqual(code, 200)
        ck = hdr.get("Set-Cookie") or ""
        self.assertIn(self.tok, ck)
        self.assertIn("HttpOnly", ck)


class JwtOnPageCookie(NoTokenless):
    """JWT mode (the default): the page served to loopback carries a cookie
    that /ui/conf and /session accept (#842 review)."""
    def setUp(self):
        super().setUp()
        self.env.stop()
        self.env = mock.patch.dict(os.environ, {"MEMBRANE_JWT": "1", "MEMBRANE_JWT_SECRET": "s" * 40})
        self.env.start()

    def test_jwt_on_served_page_sets_a_cookie_that_the_api_accepts(self):
        with mock.patch.object(UI, "enabled", lambda: True):
            code, _, hdr = self._req("/")
            ck = hdr.get("Set-Cookie") or ""
            self.assertEqual(code, 200)
            self.assertIn(UI._JWT.COOKIE + "=", ck)
            self.assertIn("HttpOnly", ck)
            cookie = {"Cookie": ck.split(";")[0]}
            self.assertEqual(self._req("/ui/conf", headers=cookie)[0], 200)
            self.assertEqual(self._req("/session", "POST", {}, cookie)[0], 200)

    # the inherited tests assume JWT off
    test_tokenless_loopback_post_session_is_401 = None
    test_seat_token_is_the_operator = None
    test_ui_cookie_is_the_operator = None
    test_jwt_is_its_sub = None
    test_operator_falls_back_to_owner = None
    test_served_page_sets_the_premined_httponly_cookie = None


if __name__ == "__main__":
    unittest.main()
