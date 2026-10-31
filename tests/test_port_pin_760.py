"""#760: a port the person passes is PINNED -- busy means a loud exit, never
a silent walk. Only the defaults walk; a restart's carried ports (#648
pin_ports) walk too, so a re-exec never dies on a lingering socket."""

import socket
import unittest

from membrane.lanes import _pick_listen_port, _pick_serve_port, port_pinned


def _listener():
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    return s


class TestPinnedPick(unittest.TestCase):
    def test_pinned_busy_listen_port_exits_naming_the_port(self):
        s = _listener()
        try:
            port = s.getsockname()[1]
            with self.assertRaises(SystemExit) as cm:
                _pick_listen_port(port, pinned=True)
            self.assertIn(str(port), str(cm.exception.code))
            self.assertIn("--listen", str(cm.exception.code))
        finally:
            s.close()

    def test_pinned_busy_serve_port_exits_naming_the_port(self):
        s = _listener()
        try:
            port = s.getsockname()[1]
            with self.assertRaises(SystemExit) as cm:
                _pick_serve_port(port, pinned=True)
            self.assertIn(str(port), str(cm.exception.code))
            self.assertIn("--serve-port", str(cm.exception.code))
        finally:
            s.close()

    def test_pinned_free_port_is_used(self):
        s = _listener()
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(_pick_listen_port(port, pinned=True), port)
        self.assertEqual(_pick_serve_port(port, pinned=True), port)

    def test_unpinned_busy_port_still_walks(self):
        s = _listener()
        try:
            port = s.getsockname()[1]
            self.assertNotEqual(_pick_listen_port(port), port)
            self.assertNotEqual(_pick_serve_port(port), port)
        finally:
            s.close()


class TestPortPinned(unittest.TestCase):
    def test_a_passed_flag_pins(self):
        self.assertTrue(port_pinned(["--serve", "--listen", "11600"], "--listen"))
        self.assertTrue(port_pinned(["--serve-port=4100"], "--serve-port"))

    def test_the_default_does_not_pin(self):
        self.assertFalse(port_pinned(["--serve"], "--listen"))
        self.assertFalse(port_pinned(["--listen-host", "127.0.0.1"], "--listen"))

    def test_a_restarts_carried_ports_do_not_pin(self):
        # pin_ports (#648) appends the held ports to the re-exec's argv: the
        # re-exec cannot tell them from the person's, so it keeps walking
        self.assertFalse(port_pinned(["--listen", "11500"], "--listen", reexec=True))


if __name__ == "__main__":
    unittest.main()
