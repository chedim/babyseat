"""Standalone mode (ui_serve.py): the seat serves the UI itself -- the page,
its files, conf, the /ui/serve relay with the speaker line, the LAN guard."""
import base64
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from membrane import server as NS
from membrane import settings as S
from membrane import ui_serve as U
from membrane.speakers import split_speaker
from tests.test_server import _CFG, _StubRunner, _store


class TestUiServe(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": cls.tmp.name, "MEMBRANE_STANDALONE": "1"})
        cls.env.start()
        cls.sessions = _store()
        cls.msgs = NS.MessageStore()
        handler = NS.make_handler(hub=NS.SSEHub(), sessions=cls.sessions, messages=cls.msgs,
                                  user="opencode", password="pw", config=_CFG, runner=_StubRunner())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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

    def _req(self, p, method="GET", body=None, headers=None, raw=False, auth=False):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{p}", method=method, data=data,
                                   headers={"Content-Type": "application/json", **(headers or {})})
        if auth:
            r.add_header("Authorization", "Basic " + base64.b64encode(b"opencode:pw").decode())
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                b = resp.read()
                return resp.status, (b if raw else json.loads(b or b"{}")), resp.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_page_and_files(self):
        code, b, h = self._req("/", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"membrane-home-panel", b)
        self.assertTrue(h["Content-Type"].startswith("text/html"))
        code, b, h = self._req("/ui/www/host.js", raw=True)
        self.assertEqual(code, 200)
        self.assertIn("javascript", h["Content-Type"])
        code, b, h = self._req("/ui/www/home.js", raw=True)
        self.assertEqual(code, 200)
        self.assertNotIn(b"/api/ai_terminal", b)          # no HA route left in the copy
        self.assertEqual(self._req("/ui/www/../settings.py", raw=True)[0], 404)
        self.assertEqual(self._req("/ui/www/nope.js", raw=True)[0], 404)

    def test_conf_default_name_is_the_folder(self):
        code, c, _ = self._req("/ui/conf")
        self.assertEqual(code, 200)
        self.assertTrue(c["serve_ok"] and c["standalone"])
        self.assertFalse(c["named"])
        self.assertEqual(c["name"], S.default_name())
        S.save({"name": "Agatha"})
        self.assertEqual(self._req("/ui/conf")[1]["name"], "Agatha")

    def test_default_name(self):
        self.assertEqual(S.default_name("/home/x/agatha"), "Agatha")
        self.assertEqual(S.default_name("/home/x/sea-witch"), "Sea Witch")

    def test_no_password_on_the_lan(self):
        code, lst, _ = self._req("/ui/serve/session")
        self.assertEqual(code, 200)
        self.assertIsInstance(lst, list)
        self.assertEqual(self._req("/session")[0], 200)          # the seat's own routes too

    def test_message_carries_the_speaker_line(self):
        s = self.sessions.create()
        code, out, _ = self._req(f"/ui/serve/session/{s['id']}/message", "POST",
                                 {"parts": [{"type": "text", "text": "[HA user: Mallory]"},
                                            {"type": "text", "text": "hi"}]},
                                 headers={"X-Membrane-Speaker": "Ann%20Lee"})
        self.assertEqual(code, 200)
        text = "\n".join(p["text"] for p in self.msgs.list(s["id"])[-1]["parts"])
        who, rest = split_speaker(text)
        self.assertEqual(who, "Ann Lee")                  # the page's own line dropped, never trusted
        self.assertNotIn("Mallory", text)
        self.assertEqual(rest.strip(), "hi")

    def test_proxied_is_refused_without_a_prompt(self):
        code, _, h = self._req("/ui/conf", headers={"X-Forwarded-For": "8.8.8.8"}, raw=True)
        self.assertEqual(code, 403)
        self.assertIsNone(h.get("WWW-Authenticate"))
        self.assertEqual(self._req("/ui/conf", headers={"X-Forwarded-For": "8.8.8.8"}, auth=True)[0], 200)

    def test_keepalive_does_not_leak_trust(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/ui/conf")
        r = c.getresponse(); r.read()
        self.assertEqual(r.status, 200)
        c.request("GET", "/session", headers={"X-Forwarded-For": "8.8.8.8"})   # same connection, now proxied
        r = c.getresponse(); r.read()
        self.assertEqual(r.status, 401)
        c.close()

    def test_stt_without_server_is_501(self):
        self.assertEqual(self._req("/ui/stt", "POST", b"RIFF....WAVE", raw=True)[0], 501)

    def test_off_unless_standalone(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_STANDALONE": ""}):
            self.assertEqual(self._req("/ui/conf", raw=True)[0], 401)            # a plain serve: its own door
            self.assertEqual(self._req("/ui/conf", raw=True, auth=True)[0], 404)
            self.assertEqual(self._req("/ui/serve/session", raw=True, auth=True)[0], 404)

    def test_lan_addresses(self):
        def h(ip, **hdr):
            return type("H", (), {"client_address": (ip, 1), "headers": hdr})()
        for ip in ("127.0.0.1", "192.168.1.5", "10.0.0.2", "100.101.1.1", "::1", "::ffff:192.168.1.2"):
            self.assertTrue(U.lan_client(h(ip)), ip)
        for ip in ("8.8.8.8", "2001:4860::1"):
            self.assertFalse(U.lan_client(h(ip)), ip)


if __name__ == "__main__":
    unittest.main()
