"""The headless serve lane: port walk, child command shape.

tests/_ef_fakes.py never sees these -- they are pure helpers; the serve
surface is OPEN (a reverse proxy is the operator's auth), so only the
port pick + child command shape are pinned here.
"""
import socket
import unittest

from membrane import opencode
from tests._ports import quiet_port


def _free_port() -> int:
    # not bind(0): an ephemeral port just released is the next one the OS
    # hands a parallel connection, between the pick and the check (#408)
    return quiet_port()


class TestServePortWalk(unittest.TestCase):
    def test_taken_port_walks_up(self):
        # held, with its upper neighbour free, below the ephemeral range: the
        # neighbour of an ephemeral port is the next one the OS hands out (#408)
        taken = quiet_port(span=2)
        hold = socket.socket()
        hold.bind(("127.0.0.1", taken))
        hold.listen(1)
        try:
            got = opencode._pick_serve_port(taken)
            # The held port must be skipped, and the walk only ever moves
            # up. It must NOT pin the exact next port: on a busy runner
            # `taken + 1` can be held by a neighbour too, so the walk lands
            # two or more steps up -- still valid, still > taken.
            self.assertGreater(got, taken)
            self.assertTrue(opencode._port_free(got))
        finally:
            hold.close()

    def test_free_port_returned_as_is(self):
        free = _free_port()
        got = opencode._pick_serve_port(free)
        # A just-freed port is normally returned unchanged. A busy runner
        # can re-take it in the gap between _free_port's close and the
        # probe, so pin only the invariant: the walk never lands below the
        # preferred port, and whatever it returns is genuinely bindable.
        self.assertGreaterEqual(got, free)
        self.assertTrue(opencode._port_free(got))


class TestLanIp(unittest.TestCase):
    def test_not_loopback_when_a_route_exists(self):
        ip = opencode._lan_ip()
        self.assertTrue(ip)
        self.assertNotIn(" ", ip)


if __name__ == "__main__":
    unittest.main()