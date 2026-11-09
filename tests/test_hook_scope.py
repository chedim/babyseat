"""The suite's autouse credential hook (conftest) stands in for TEST clients only (#842)."""
import http.client
import http.server
import threading
import unittest


class TestInternalCallerNeedsOwnCredential(unittest.TestCase):
    """The suite's autouse credential hook is for TEST clients only: a seat-code
    caller that forgets seatauth.loopback_headers must fail with 401 (#842)."""

    def test_internal_caller_without_loopback_headers_fails(self):
        import http.client
        seen = {}

        class _H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                seen["auth"] = self.headers.get("Authorization")
                self.send_response(200); self.end_headers()

            def log_message(self, *a): pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            # a test client gets the hook's credential ...
            c = http.client.HTTPConnection("127.0.0.1", srv.server_port); c.request("GET", "/"); c.getresponse().read()
            self.assertTrue(seen["auth"], "test client should carry the hook credential")
            # ... a call made from inside membrane/* does not
            from membrane import peers
            ns = {}
            code = compile("import http.client\ndef go(port):\n c=http.client.HTTPConnection('127.0.0.1',port); c.request('GET','/'); c.getresponse().read()",
                           peers.__file__, "exec")
            exec(code, ns)
            seen.clear(); ns["go"](srv.server_port)
            self.assertIsNone(seen.get("auth"))
        finally:
            srv.shutdown()
