"""A turn cut while the proxy is still in its gate ends now (2026-10-31: the gate waited
minutes on the field lock; the stop button and the watchdog only closed the BACKEND
socket, which did not exist yet, so the session stayed busy)."""
import socket
import threading
import time
import types
import unittest

from membrane import server as SRV


def _silent_server():
    """Accepts and reads, never answers (a proxy stuck in its gate)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(4)
    held = []

    def run():
        while True:
            try:
                c, _ = s.accept()
            except OSError:
                return
            held.append(c)
    threading.Thread(target=run, daemon=True).start()
    return s, held


class TestAbortInTheGate(unittest.TestCase):
    def test_the_kill_cuts_a_round_still_waiting_for_the_proxy(self):
        s, held = _silent_server()
        self.addCleanup(s.close)
        runner = types.SimpleNamespace(_current_turn_sid="ses_x", _turn_sockets={})
        ladder = SRV.loopback_ladder(s.getsockname()[1], runner=runner)
        out = {}

        def go():
            t0 = time.time()
            try:
                ladder.stream({"model": "m", "messages": []}, lambda *a: None)
            except Exception as e:
                out["err"] = e
            out["s"] = time.time() - t0
        th = threading.Thread(target=go, daemon=True)
        th.start()
        for _ in range(100):
            if runner._turn_sockets.get("ses_x") is not None:
                break
            time.sleep(0.02)
        self.assertIsNotNone(runner._turn_sockets.get("ses_x"), "the round's socket is registered")
        SRV.TurnRunner._kill_turn_socket(runner, "ses_x")
        th.join(5)
        self.assertFalse(th.is_alive(), "the cut round is still blocked")
        self.assertIn("err", out)
        self.assertLess(out["s"], 5)


if __name__ == "__main__":
    unittest.main()
