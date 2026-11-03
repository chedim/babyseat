"""THE TEARDOWN STOPS TAKING REQUESTS FIRST (2026-11-02: the main seat's teardown was cut at
90 s inside EntityStore.close; the stack dump had 13 request threads -- the loops' code_run,
still being served -- each saving the whole entity store after its close). Both listeners
(the /v1 proxy and the serve port) stop accepting before capture, the field and the stores
close; requests already running finish, no new one starts.
"""
import os
import socket
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import opencode as OC  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


class _H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def _serving():
    h = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=h.serve_forever, daemon=True).start()
    return h


def _refused(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return False
    except OSError:
        return True


class ListenersClose(unittest.TestCase):
    def test_a_serving_listener_stops_accepting(self):
        h = _serving()
        port = h.server_address[1]
        self.assertFalse(_refused(port))
        t0 = time.monotonic()
        OC._close_listeners([(h, True)])
        self.assertLess(time.monotonic() - t0, 3)
        self.assertTrue(_refused(port))

    def test_a_listener_not_looping_closes_without_waiting(self):
        h = ThreadingHTTPServer(("127.0.0.1", 0), _H)   # launch mode: no serve_forever thread
        port = h.server_address[1]
        t0 = time.monotonic()
        OC._close_listeners([(h, False)])
        self.assertLess(time.monotonic() - t0, 1)
        self.assertTrue(_refused(port))

    def test_none_and_errors_are_skipped(self):
        OC._close_listeners([(None, True), (object(), True)])


class TheTeardownOrder(unittest.TestCase):
    def test_listeners_close_before_capture_and_the_stores(self):
        fin = SRC[SRC.index("# THE TEARDOWN'S BOUND"):]
        lis = fin.index("_close_listeners(")
        self.assertLess(lis, fin.index("_cap.stop("))
        self.assertLess(lis, fin.index("_fld.flush()"))
        self.assertIn('_td_mark("listeners closed")', fin)

    def test_the_serve_server_is_kept_for_the_teardown(self):
        self.assertRegex(SRC, r"runner\._httpd\s*,\s*_\s*=\s*start\(")


if __name__ == "__main__":
    unittest.main()
