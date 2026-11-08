"""#842 / PR 896: in JWT mode a chat owned by the MEMBRANE_OPERATOR identity stays
visible to the operator's token (whose sub differs), _is_operator treats the sub and
MEMBRANE_OPERATOR as one person, and POST /session reuses the existing chat."""
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_no_tokenless_842 as T  # noqa: E402

UI, NS = T.UI, T.NS


class OperatorIdentity(T.NoTokenless):
    def setUp(self):
        super().setUp()
        self.who = types.SimpleNamespace(sub="sub-123", name="alice")
        self.p = mock.patch.object(UI, "token_person", lambda h: self.who)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        super().tearDown()

    def _op_chat(self):
        s = self.sessions.create()
        self.sessions.sessions[s["id"]]["owner"] = "alice"   # made by the cookie/seat token
        return s["id"]

    def test_operator_chat_stays_listed(self):
        sid = self._op_chat()
        code, lst, _ = self._req("/session", "GET", None, {"Authorization": "Bearer jwt"})
        self.assertEqual(code, 200)
        self.assertIn(sid, [s["id"] for s in lst])

    def test_is_operator_one_person(self):
        from membrane import lanes
        self._req("/session", "GET", None, {"Authorization": "Bearer jwt"})   # the token is seen
        self.assertTrue(lanes._is_operator("alice"))
        self.assertTrue(lanes._is_operator("sub-123"))

    def test_post_session_reuses_operator_chat(self):
        sid = self._op_chat()
        code, s, _ = self._req("/session", "POST", {}, {"Authorization": "Bearer jwt"})
        self.assertEqual((code, s.get("id")), (200, sid))

    test_tokenless_loopback_post_session_is_401 = None
    test_seat_token_is_the_operator = None
    test_ui_cookie_is_the_operator = None
    test_jwt_is_its_sub = None
    test_operator_falls_back_to_owner = None
    test_served_page_sets_the_premined_httponly_cookie = None


if __name__ == "__main__":
    unittest.main()
