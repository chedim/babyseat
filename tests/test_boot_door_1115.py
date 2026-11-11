"""THE SERVE PORT ANSWERS WHILE THE SEAT BOOTS (#1115): the port was reserved (bound + listening) at
the top of the boot but nobody answered it for minutes -- the browser showed a dead page, requests
timed out. A small door answers on the reserved socket until the native serve takes the socket over:
every request gets a 503 with the step the boot is on (a page that reloads itself for a browser,
JSON for everything else), so nothing -- Home Assistant's /global/health readiness included -- ever
reads a half-booted seat as ready.
"""
import json
import socket
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.bootprof import BootDoor, Laps  # noqa: E402

SRC = (Path(__file__).resolve().parent.parent / "membrane" / "opencode.py").read_text()


def _reserved():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(128)
    return s


def _get(port, path, method="GET", accept="application/json"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 headers={"Accept": accept},
                                 data=b"{}" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


class _Ready(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ready")

    def log_message(self, *a):
        pass


class TheDoor(unittest.TestCase):
    def setUp(self):
        self.sock = _reserved()
        self.port = self.sock.getsockname()[1]
        self.laps = Laps("boot")
        self.laps.step("field rows")
        self.door = BootDoor(self.sock, self.laps)

    def tearDown(self):
        self.door.handover()
        self.sock.close()

    def test_health_is_503_with_the_step(self):
        code, headers, body = _get(self.port, "/global/health")
        self.assertEqual(code, 503)
        self.assertIn("Retry-After", headers)
        got = json.loads(body)
        self.assertTrue(got["loading"])
        self.assertEqual(got["step"], "field rows")

    def test_any_api_route_and_method_is_503(self):
        for path, method in (("/session", "GET"), ("/session", "POST"), ("/membrane/state", "GET")):
            self.assertEqual(_get(self.port, path, method)[0], 503, (path, method))

    def test_a_browser_gets_a_page_that_names_the_step_and_reloads(self):
        self.laps.step("entities")
        code, headers, body = _get(self.port, "/", accept="text/html,application/xhtml+xml")
        self.assertEqual(code, 503)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/html"))
        self.assertIn("loading the field", body)
        self.assertIn("entities", body)
        self.assertIn('http-equiv="refresh"', body)

    def test_the_step_name_is_escaped(self):
        self.laps.step("<b>x</b>")
        body = _get(self.port, "/", accept="text/html")[2]
        self.assertNotIn("<b>x</b>", body)

    def test_handover_gives_the_same_socket_to_the_real_server(self):
        self.assertEqual(_get(self.port, "/global/health")[0], 503)
        self.door.handover()
        real = ThreadingHTTPServer(("127.0.0.1", self.port), _Ready, bind_and_activate=False)
        real.socket = self.sock                 # what server.start() does with the reservation
        real.server_activate()
        threading.Thread(target=real.serve_forever, daemon=True).start()
        try:
            code, _, body = _get(self.port, "/global/health")
            self.assertEqual((code, body), (200, "ready"))
        finally:
            real.shutdown()

    def test_handover_twice_is_harmless(self):
        self.door.handover()
        self.door.handover()


class ARealBoot(unittest.TestCase):
    """A throwaway seat (RAM field, a fake Ollama, quiet ports, its own temp dir): from the moment
    the serve port is reserved, every request is answered -- 503 'loading' or, once up, 200 -- and
    never left hanging; the boot ends with its [boot] line."""

    def test_the_port_never_hangs_and_the_boot_profiles_itself(self):
        import os
        import shutil
        import subprocess
        import tempfile
        import time
        here = os.path.dirname(__file__)
        sys.path.insert(0, here)
        from _fake_ollama import FakeOllama
        from _ports import quiet_port
        from _seat_env import seat_env
        port = quiet_port()
        listen = quiet_port(avoid=(port,))
        seat_dir = tempfile.mkdtemp(prefix="boot-door-seat-")
        env = seat_env(seat_dir, dict(os.environ, MEMBRANE_EFIELD_STORE="mem", MEMBRANE_LAN="0",
                                      MEMBRANE_EF_ANN="0", MEMBRANE_NO_OPEN="1", MEMBRANE_RERANK="0"))
        py = os.path.join(here, "..", ".venv", "bin", "python")
        fake = FakeOllama().__enter__()
        se = open(os.path.join(seat_dir, "seat.stderr"), "wb")
        proc = subprocess.Popen(
            [py if os.path.exists(py) else sys.executable, "-m", "membrane.opencode",
             "--serve", "--serve-port", str(port), "--serve-host", "127.0.0.1",
             "--no-launch", "--no-thinker", "--no-dreamer", "--no-autonode", "--no-router",
             "--model", "granite4:latest", "--field", f"{seat_dir}/f.json",
             "--audit", f"{seat_dir}/a.jsonl", "--backend", fake.url, "--listen", str(listen)],
            env=env, cwd=seat_dir, stdout=subprocess.DEVNULL, stderr=se)
        seen = []
        try:
            deadline = time.time() + 60
            while time.time() < deadline and proc.poll() is None:
                try:
                    code, _, body = _get(port, "/global/health")
                except (ConnectionRefusedError, urllib.error.URLError) as e:
                    reason = getattr(e, "reason", e)
                    self.assertIsInstance(reason, ConnectionRefusedError, f"the port hung: {e!r}")
                    time.sleep(0.05)
                    continue
                seen.append(code)
                if code == 200:
                    break
                self.assertEqual(code, 503)
                self.assertTrue(json.loads(body)["loading"])
                time.sleep(0.05)
            self.assertEqual(seen[-1:], [200], seen)
            deadline = time.time() + 10
            while time.time() < deadline:
                se.flush()
                log = open(os.path.join(seat_dir, "seat.stderr"), "rb").read().decode(errors="replace")
                if "[boot] " in log:
                    break
                time.sleep(0.1)
            self.assertRegex(log, r"\[boot\] [\d.]+s: .*field rows [\d.]+s \(cpu [\d.]+s\), .*serve [\d.]+s")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            se.close()
            fake.__exit__(None, None, None)
            shutil.rmtree(seat_dir, ignore_errors=True)


class TheSeatOpensIt(unittest.TestCase):
    def test_the_door_opens_on_the_reservation_and_hands_over_before_the_serve(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        opened = main.index("BootDoor(_serve_socket")
        self.assertGreater(opened, main.index("_serve_socket.listen(128)"))
        self.assertLess(opened, main.index("_boot_laps.step(\"field rows\")"))
        handed = main.index("_boot_door.handover()")
        self.assertLess(handed, main.index("runner._httpd, _ = start("))

    def test_a_failed_serve_closes_the_door_too(self):
        main = SRC[SRC.index("\ndef main() -> None:"):]
        failed = main.index("native serve FAILED")
        self.assertIn("_boot_door.handover()", main[failed - 400:failed])


if __name__ == "__main__":
    unittest.main()
