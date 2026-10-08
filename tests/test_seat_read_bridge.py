"""THE FIELD'S WINDOW (2026-10-08, red first): the HA field dashboard and
its sensors need the seat's read surfaces (/membrane/state, the universe
map's cloud/entities/axes, the entity drawer's read/related) -- but they
live on the /v1 proxy (:11500), bound to 127.0.0.1 with NO auth and with
WRITE routes beside them. The LAN-facing, basic-authed serve (:4096)
relays exactly the read-only allow-list, GET only; everything else on the
proxy stays unreachable from outside.
"""
import base64
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import membrane.server as MS  # noqa: E402


class _Seat(BaseHTTPRequestHandler):
    """A fake /v1 proxy: echoes what it was asked."""
    seen: list = []

    def do_GET(self):
        _Seat.seen.append(("GET", self.path))
        body = json.dumps({"echo": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        _Seat.seen.append(("POST", self.path))
        self.send_response(500)
        self.end_headers()

    def log_message(self, *a):
        pass


class TestReadUrl(unittest.TestCase):
    def test_allow_list_maps_to_the_local_proxy(self):
        srv = type("S", (), {"server_address": ("127.0.0.1", 11500)})()
        self.assertEqual(MS.seat_read_url(srv, "/membrane/state"),
                         "http://127.0.0.1:11500/membrane/state")
        self.assertEqual(MS.seat_read_url(srv, "/membrane/map/cloud?x=a&y=b"),
                         "http://127.0.0.1:11500/membrane/map/cloud?x=a&y=b")
        for p in ("/membrane/map/entities", "/membrane/map/axes",
                  "/membrane/map/field?m=800", "/membrane/field/entity?eid=w%3Aa",
                  "/membrane/map/distance?eid=w%3Aa",
                  "/membrane/entity/read?spec=x", "/membrane/entity-related?q=x"):
            self.assertTrue(MS.seat_read_url(srv, p), p)

    def test_everything_else_stays_closed(self):
        srv = type("S", (), {"server_address": ("0.0.0.0", 11500)})()
        for p in ("/membrane/entity/add", "/membrane/run", "/v1/chat/completions",
                  "/membrane/statex", "/membrane/state/../entity/add",
                  "/viewer", "/membrane/intend", "/membrane/learn"):
            self.assertIsNone(MS.seat_read_url(srv, p), p)

    def test_no_seat_no_url(self):
        self.assertIsNone(MS.seat_read_url(None, "/membrane/state"))
        self.assertIsNone(MS.seat_read_url(object(), "/membrane/state"))


class TestRelay(unittest.TestCase):
    def setUp(self):
        _Seat.seen = []
        self.seat = ThreadingHTTPServer(("127.0.0.1", 0), _Seat)
        threading.Thread(target=self.seat.serve_forever, daemon=True).start()
        handler = MS.make_handler(
            hub=MS.SSEHub(), sessions=MS.SessionStore(),
            messages=MS.MessageStore(), user="u", password="p", config={},
            runner=None, srv=self.seat)
        self.serve = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.serve.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.serve.server_address[1]}"
        self.auth = {"Authorization": "Basic " + base64.b64encode(b"u:p").decode()}

    def tearDown(self):
        self.serve.shutdown()
        self.seat.shutdown()

    def _get(self, path, auth=True, method="GET"):
        req = urllib.request.Request(self.base + path, method=method,
                                     headers=self.auth if auth else {},
                                     data=b"{}" if method == "POST" else None)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, None

    def test_relays_the_read(self):
        st, body = self._get("/membrane/map/cloud?x=pca1&y=pca2")
        self.assertEqual(st, 200)
        self.assertEqual(body, {"echo": "/membrane/map/cloud?x=pca1&y=pca2"})

    def test_needs_the_serve_login(self):
        st, _ = self._get("/membrane/state", auth=False)
        self.assertEqual(st, 401)
        self.assertEqual(_Seat.seen, [])

    def test_writes_never_relay(self):
        st, _ = self._get("/membrane/entity/add")
        self.assertEqual(st, 404)
        self._get("/membrane/state", method="POST")
        self.assertNotIn(("POST", "/membrane/state"), _Seat.seen)


if __name__ == "__main__":
    unittest.main()
