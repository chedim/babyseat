"""JWT mode: a bare loopback caller (this machine, no forwarding header, no
token) is the seat itself, as with JWT off; anything else still needs a token."""
import io
import os
import unittest
from unittest import mock

from membrane import jwtauth as J
from membrane import seatauth as SA
from membrane import ui_serve as UI

SECRET = "s" * 40


class _H:
    def __init__(self, ip="127.0.0.1", headers=None):
        self.client_address = (ip, 50000)
        self.headers = headers or {}
        self.command = "GET"
        self.wfile = io.BytesIO()
        self.code = None

    def send_response(self, code):
        self.code = code

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


class LoopbackIsLocal(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"MEMBRANE_JWT": "1", "MEMBRANE_JWT_SECRET": SECRET})
        p.start()
        self.addCleanup(p.stop)

    def test_a_bare_loopback_caller_passes_as_the_seat(self):
        for ip in ("127.0.0.1", "::1"):
            h = _H(ip)
            self.assertTrue(J.gate(h))
            self.assertTrue(h._identity.internal)
            self.assertTrue(SA.owner(h))
            self.assertIsNone(UI.token_person(h))     # keeps its own speaker line

    def test_a_forwarded_loopback_caller_needs_a_token(self):
        h = _H(headers={"X-Forwarded-For": "10.0.0.5"})
        self.assertFalse(J.gate(h))
        self.assertEqual(h.code, 401)

    def test_an_off_box_caller_needs_a_token(self):
        h = _H("192.168.1.20")
        self.assertFalse(J.gate(h))
        self.assertEqual(h.code, 401)

    def test_a_bad_token_on_loopback_is_refused(self):
        h = _H(headers={"Authorization": "Bearer nope"})
        self.assertFalse(J.gate(h))
        self.assertEqual(h.code, 401)

    def test_an_ambiguous_cookie_on_loopback_is_refused(self):
        a, b = J.mint(SECRET, "alice"), J.mint(SECRET, "bob")
        h = _H(headers={"Cookie": f"membrane_jwt={a}; membrane_jwt={b}"})
        self.assertFalse(J.gate(h, cookie=True))
        self.assertEqual(h.code, 401)

    def test_the_page_asks_a_local_callers_name(self):
        h = _H()
        h.path = "/ui/conf"
        self.assertTrue(J.gate(h, cookie=True))
        seen = {}
        with mock.patch.object(UI, "_json", lambda _h, c: seen.update(c)), \
                mock.patch.object(UI, "conf", lambda: {}), \
                mock.patch.object(UI, "enabled", lambda: True):
            UI.intercept(h)
        self.assertEqual(seen.get("auth"), "jwt")
        self.assertIsNone(seen.get("who"))

    def test_a_person_token_on_loopback_is_that_person(self):
        h = _H(headers={"Authorization": "Bearer " + J.mint(SECRET, "alice", name="Alice")})
        self.assertTrue(J.gate(h))
        self.assertEqual(h._identity.sub, "alice")

    def test_an_in_process_call_without_a_socket_is_not_waved_through(self):
        h = _H()
        del h.client_address
        self.assertFalse(J.gate(h))


if __name__ == "__main__":
    unittest.main()
