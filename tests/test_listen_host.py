"""THE /v1 PORT ON THE LAN (2026-10-08, the operator's call: "it should bind
to all interfaces (the device is Wifi only) ... fully open on the lan for
now"): the proxy (:11500) bound 127.0.0.1 hardcoded; it binds 0.0.0.0 by
default now, --listen-host / MEMBRANE_LISTEN_HOST turn it back (127.0.0.1).
NOTE: :11500 carries UNAUTHENTICATED write + exec routes -- open on the LAN
is the operator's explicit choice."""
import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from membrane import opencode as OC  # noqa: E402


class TestListenHost(unittest.TestCase):
    def test_default_is_all_interfaces(self):
        os.environ.pop("MEMBRANE_LISTEN_HOST", None)
        self.assertEqual(OC.proxy_bind_host(types.SimpleNamespace(listen_host=None)), "0.0.0.0")

    def test_flag_and_env_turn_it_back(self):
        self.assertEqual(OC.proxy_bind_host(types.SimpleNamespace(listen_host="127.0.0.1")), "127.0.0.1")
        os.environ["MEMBRANE_LISTEN_HOST"] = "127.0.0.1"
        try:
            self.assertEqual(OC.proxy_bind_host(types.SimpleNamespace(listen_host=None)), "127.0.0.1")
        finally:
            del os.environ["MEMBRANE_LISTEN_HOST"]


if __name__ == "__main__":
    unittest.main()
