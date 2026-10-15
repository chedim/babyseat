"""THE TUI-AS-CLIENT lane (stage 3.5): with the native serve surface up,
`membrane opencode`'s child is not a full opencode seat fronting /v1 with
its own turn loop and its own local storage -- it is a thin ATTACH client
(`opencode attach http://127.0.0.1:<serve-port>`) over membrane's
opencode-compatible REST/SSE server: sessions and messages live in
membrane's stores, turns run in membrane's TurnRunner, tool parts stream
from the SSE hub. opencode is display only (the operator's watchpoint),
not a surface with memory. Pins: the attach command shape (-c rides,
never a --model auto-add: the SERVER picks the model) and the child env
(auth pair in, OPENCODE_CONFIG/..._CONTENT out).
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.opencode import _attach_cmd, _attach_env


class TestAttachLane(unittest.TestCase):
    def test_cmd_is_attach_to_loopback(self):
        self.assertEqual(
            _attach_cmd("/usr/bin/opencode", 4107),
            ["/usr/bin/opencode", "attach", "http://127.0.0.1:4107"])

    def test_passthrough_rides_last(self):
        self.assertEqual(
            _attach_cmd("opencode", 4096, ["--dir", "/repo"]),
            ["opencode", "attach", "http://127.0.0.1:4096",
             "--dir", "/repo"])

    def test_cont_appends_dash_c_once(self):
        self.assertEqual(_attach_cmd("opencode", 4096, [], cont=True)[-1],
                         "-c")
        # an explicit session choice in passthrough wins; no auto -c
        self.assertNotIn("-c", _attach_cmd("opencode", 4096, ["-s"],
                                           cont=True))
        self.assertIn("-c", _attach_cmd("opencode", 4096, ["-c"], cont=True))

    def test_env_drops_config(self):
        e = _attach_env({"OPENCODE_CONFIG": "/tmp/cfg.json", "PATH": "/bin"})
        # the TUI must not front its own /v1 lane: config out, env else intact
        self.assertNotIn("OPENCODE_CONFIG", e)
        self.assertNotIn("OPENCODE_CONFIG_CONTENT", e)
        self.assertEqual(e["PATH"], "/bin")


if __name__ == "__main__":
    unittest.main()