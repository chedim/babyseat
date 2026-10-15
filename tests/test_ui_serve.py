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
                                  config=_CFG, runner=_StubRunner())
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

    def test_the_panel_path_serves_the_page(self):
        # THE OPERATOR'S EXHIBIT (2026-10-13): the page's landing hop writes
        # /membrane into the address bar (the HA panel's url_path); a reload
        # of it used to 404 on the standalone serve -- HA serves that URL,
        # the seat didn't. Both spellings load the page now.
        code, b, h = self._req("/membrane", raw=True)
        self.assertEqual(code, 200)
        self.assertIn(b"membrane-home-panel", b)
        self.assertTrue(h["Content-Type"].startswith("text/html"))

    def test_conf_is_bob_until_named(self):
        code, c, _ = self._req("/ui/conf")
        self.assertEqual(code, 200)
        self.assertTrue(c["serve_ok"] and c["standalone"])
        self.assertFalse(c["named"])
        self.assertEqual(c["name"], "Bob")
        S.save({"name": "Juniper"})
        self.assertEqual(self._req("/ui/conf")[1]["name"], "Juniper")

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

    def test_a_person_sees_their_own_chats_not_the_loops(self):
        self.sessions.create(title="watch: session:now")            # a loop's, made by the seat
        A = {"X-Membrane-Speaker": "Ann"}
        code, mine, _ = self._req("/ui/serve/session", "POST", {"title": "hi"}, headers=A)
        self.assertEqual(mine["owner"], "Ann")
        self._req("/ui/serve/session", "POST", {"title": "bob's"}, headers={"X-Membrane-Speaker": "Bob"})
        lst = self._req("/ui/serve/session", headers=A)[1]
        self.assertEqual([x["id"] for x in lst], [mine["id"]])
        self.assertGreaterEqual(len(self._req("/session")[1]), 3)  # the seat's own list is whole

    def test_proxied_is_refused(self):
        code, _, h = self._req("/ui/conf", headers={"X-Forwarded-For": "8.8.8.8"}, raw=True)
        self.assertEqual(code, 403)
        self.assertIsNone(h.get("WWW-Authenticate"))           # never a login prompt
        # auth is gone: a stale basic-auth header changes nothing, the proxy stays refused
        self.assertEqual(self._req("/ui/conf", headers={"X-Forwarded-For": "8.8.8.8"}, auth=True)[0], 403)

    def test_keepalive_does_not_leak_lan_trust(self):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/ui/conf")                          # local: trusted, 200
        r = c.getresponse(); r.read()
        self.assertEqual(r.status, 200)
        c.request("GET", "/ui/conf", headers={"X-Forwarded-For": "8.8.8.8"})   # same connection, now proxied
        r = c.getresponse(); r.read()
        self.assertEqual(r.status, 403)                       # the LAN guard still refuses
        c.close()

    def test_page_voice_log(self):
        self.assertEqual(self._req("/ui/log", "POST", {"who": "you", "what": "dictation", "text": "{}"}, raw=True)[0], 204)
        self.assertIn("dictation", (S.data_dir() / "page-voice.log").read_text())

    def test_stt_without_server_is_501(self):
        self.assertEqual(self._req("/ui/stt", "POST", b"RIFF....WAVE", raw=True)[0], 501)

    def test_off_unless_standalone(self):
        with mock.patch.dict(os.environ, {"MEMBRANE_STANDALONE": ""}):
            self.assertEqual(self._req("/ui/conf", raw=True)[0], 404)            # a plain serve: no UI route
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
