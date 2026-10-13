"""THE PORT THAT WALKED (2026-10-08, the ThinkPhone): after every restart
the seat booted its proxy on :11501 -- the probe's bare bind failed on the
TIME_WAIT leftovers of its own loops' connections to :11500, while the
real server (allow_reuse_address) would have bound fine. The probe now
binds like the server: TIME_WAIT is free, a live listener is busy."""

import socket
import unittest

from membrane.lanes import _port_free, _pick_listen_port, _pick_serve_port


def _listener():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    return s


class TestPortProbe(unittest.TestCase):
    def test_live_listener_is_busy(self):
        s = _listener()
        try:
            port = s.getsockname()[1]
            self.assertFalse(_port_free(port))
            self.assertNotEqual(_pick_listen_port(port), port)
        finally:
            s.close()

    def test_a_wildcard_listener_is_busy_for_the_wildcard(self):
        # THE SILENT BOOT DEATH (2026-10-13): the probe bound 127.0.0.1 and
        # a loopback probe BLESSES the port while the wildcard is held --
        # the lab seat's proxy lives on *:11500, the next seat's default
        # --listen 11500 probe passed it by and the real 0.0.0.0 bind died
        # Errno 48, silently (launch mode owns stderr). The probe now asks
        # the address the real bind will use.
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", 0))
        s.listen(4)
        try:
            port = s.getsockname()[1]
            self.assertFalse(_port_free(port, host="0.0.0.0"))     # wildcard bind: busy
            self.assertNotEqual(_pick_listen_port(port, host="0.0.0.0"), port)
            self.assertNotEqual(_pick_serve_port(port, host="0.0.0.0"), port)
        finally:
            s.close()

    def test_time_wait_leftovers_are_free(self):
        s = _listener()
        port = s.getsockname()[1]
        c = socket.create_connection(("127.0.0.1", port))
        conn, _ = s.accept()
        conn.close()            # the server side closes first: TIME_WAIT
        c.close()
        s.close()
        self.assertTrue(_port_free(port))


if __name__ == "__main__":
    unittest.main()
