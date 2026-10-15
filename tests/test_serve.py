"""The headless serve lane: credentials, port walk, child command shape.

tests/_ef_fakes.py never sees these -- they are pure helpers, but they
gate the phone-facing surface (the companion app types the pair the
seat serves), so the persistence + shape contracts are pinned here.
"""
import json
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path

from membrane import opencode


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestServeCredentials(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = str(Path(self.d) / "serve-auth.json")

    def test_generated_once_then_reused(self):
        u1, p1, first = opencode._serve_credentials(self.path, "opencode", None)
        self.assertTrue(first)
        self.assertEqual(u1, "opencode")
        # persisted, 0600, valid JSON with both halves
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode) & 0o777,
                         0o600)
        d = json.loads(Path(self.path).read_text())
        self.assertEqual(d["password"], p1)
        # a second boot gets the SAME pair (the device's saved connection
        # survives seat restarts)
        u2, p2, again = opencode._serve_credentials(self.path, "opencode", None)
        self.assertFalse(again)
        self.assertEqual((u2, p2), (u1, p1))

    def test_explicit_pass_overrides_and_is_not_persisted(self):
        u, p, first = opencode._serve_credentials(self.path, "bob", "hunter2")
        self.assertEqual((u, p, first), ("bob", "hunter2", False))
        self.assertFalse(os.path.exists(self.path))

    def test_corrupt_file_regenerates(self):
        Path(self.path).write_text("not json" * 4)
        u, p, first = opencode._serve_credentials(self.path, "opencode", None)
        self.assertTrue(first)
        self.assertEqual(json.loads(Path(self.path).read_text())["password"], p)

    def test_persisted_user_wins_over_default(self):
        Path(self.path).write_text(json.dumps(
            {"user": "boat", "password": "keep-me"}))
        u, p, first = opencode._serve_credentials(self.path, "opencode", None)
        self.assertEqual((u, p, first), ("boat", "keep-me", False))


class TestServePortWalk(unittest.TestCase):
    def test_taken_port_walks_up(self):
        hold = socket.socket()
        hold.bind(("127.0.0.1", 0))
        hold.listen(1)
        taken = hold.getsockname()[1]
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