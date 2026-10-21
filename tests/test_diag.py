"""Diagnosing a slow seat without root (#346, membrane/diag.py): a per-request
timing ring on both HTTP servers (:4096 serve, :11500 proxy) with a slow-line
log, its summary at /membrane/diag/timing, and an in-process sampling profiler
at /membrane/diag/profile that writes flamegraph-collapsed stacks.

No stderr from threads here: the slow line goes to a list (diag._emit is
swapped), so no test can trip the buffered-stderr guard (#121)."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from membrane import diag as D
from membrane import server as NS
from tests.test_server import _CFG, _StubRunner, _store


def _req(port, p, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"http://127.0.0.1:{port}{p}", method=method, data=data,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            b = resp.read()
            return resp.status, json.loads(b or b"{}")
    except urllib.error.HTTPError as e:
        b = e.read()
        try:
            return e.code, json.loads(b or b"{}")
        except ValueError:
            return e.code, {}


class _DiagCase(unittest.TestCase):
    """A fresh diag state per test, the slow line captured, the data dir a tmp."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"MEMBRANE_DATA_DIR": self.tmp.name})
        self.env.start()
        os.environ.pop("MEMBRANE_TIMING", None)
        os.environ.pop("MEMBRANE_TIMING_SLOW_MS", None)
        D.reset()
        self.lines = []
        self.emit = mock.patch.object(D, "_emit", self.lines.append)
        self.emit.start()

    def tearDown(self):
        D.profile_wait(10)
        self.emit.stop()
        D.reset()
        self.env.stop()
        self.tmp.cleanup()


class TestTimingUnit(_DiagCase):
    def test_off_by_default_and_env_switches_it_on(self):
        self.assertFalse(D.timing_on())
        with mock.patch.dict(os.environ, {"MEMBRANE_TIMING": "1"}):
            D.reset()
            self.assertTrue(D.timing_on())

    def test_record_and_summary(self):
        D.set_timing(True)
        for ms in (10, 20, 30, 40, 1000):
            D.record(4096, "GET", "/membrane/self/card?x=1", 200, ms, "Thread-1")
        D.record(11500, "GET", "/membrane/state", 200, 5, "Thread-2")
        s = D.summary()
        self.assertTrue(s["on"])
        card = s["routes"]["4096 GET /membrane/self/card"]
        self.assertEqual(card["count"], 5)
        self.assertEqual(card["max"], 1000)
        self.assertEqual(card["total"], 1100)
        self.assertEqual(card["p50"], 30)
        self.assertEqual(card["p90"], 1000)
        self.assertIn("11500 GET /membrane/state", s["routes"])
        self.assertEqual(s["recent"][-1]["thread"], "Thread-2")

    def test_ids_in_paths_collapse_to_one_route(self):
        D.set_timing(True)
        D.record(4096, "GET", "/session/ses_0a1b2c3d4e5f6/message", 200, 3, "t")
        D.record(4096, "GET", "/session/ses_99ffee8877aa1/message", 200, 3, "t")
        self.assertEqual(list(D.summary()["routes"]), ["4096 GET /session/*/message"])

    def test_off_records_nothing(self):
        D.record(4096, "GET", "/x", 200, 5000, "t")
        self.assertEqual(D.summary()["routes"], {})
        self.assertEqual(self.lines, [])

    def test_slow_line_only_over_the_bar(self):
        D.set_timing(True, slow_ms=100)
        D.record(4096, "GET", "/fast", 200, 50, "t")
        D.record(4096, "GET", "/slow", 200, 150, "Thread-7")
        self.assertEqual(len(self.lines), 1)
        self.assertTrue(self.lines[0].startswith("[timing] "))
        self.assertIn("/slow", self.lines[0])
        self.assertIn("Thread-7", self.lines[0])

    def test_the_ring_is_bounded(self):
        D.set_timing(True)
        for i in range(D.TIMING_RING + 50):
            D.record(4096, "GET", "/x", 200, 1, "t")
        self.assertEqual(D.summary()["routes"]["4096 GET /x"]["count"], D.TIMING_RING)


def _spin_346(stop):
    x = 0
    while not stop.is_set():
        x += 1


class TestProfilerUnit(_DiagCase):
    def test_samples_a_busy_thread_and_writes_the_file(self):
        stop = threading.Event()
        t = threading.Thread(target=_spin_346, args=(stop,), name="busy-346", daemon=True)
        t.start()
        try:
            ok, _ = D.profile_start(0.5)
            self.assertTrue(ok)
            self.assertTrue(D.profile_wait(10))
        finally:
            stop.set()
            t.join(5)
        st = D.profile_status()
        self.assertFalse(st["running"])
        self.assertGreater(st["samples"], 0)
        self.assertIn("busy-346", st["threads"])
        self.assertTrue(any("_spin_346" in r["stack"] and r["stack"].startswith("busy-346;")
                            for r in st["top"]), st["top"][:5])
        self.assertLessEqual(len(st["top"]), D.PROFILE_TOP)
        path = st["file"]
        self.assertTrue(path.startswith(os.path.join(self.tmp.name, "diag", "profile-")))
        self.assertTrue(path.endswith(".collapsed"))
        with open(path) as f:
            rows = f.read().splitlines()
        self.assertTrue(rows)
        stack, n = rows[0].rsplit(" ", 1)         # flamegraph.pl's collapsed line
        self.assertGreater(int(n), 0)
        self.assertIn(";", stack)
        # the profiler never samples itself
        self.assertFalse(any(r["stack"].startswith(D.PROFILER_THREAD) for r in st["top"]))

    def test_one_at_a_time(self):
        ok, _ = D.profile_start(0.5)
        self.assertTrue(ok)
        ok2, why = D.profile_start(0.5)
        self.assertFalse(ok2)
        self.assertIn("running", why)
        self.assertTrue(D.profile_wait(10))

    def test_seconds_are_capped(self):
        self.assertEqual(D.clamp_seconds(10_000), D.PROFILE_MAX_S)
        self.assertEqual(D.clamp_seconds(None), D.PROFILE_DEFAULT_S)
        self.assertEqual(D.clamp_seconds("junk"), D.PROFILE_DEFAULT_S)
        self.assertGreater(D.clamp_seconds(-5), 0)


class TestServeEndpoints(_DiagCase):
    """:4096 -- the opencode-compatible serve handler."""

    def setUp(self):
        super().setUp()
        handler = NS.make_handler(hub=NS.SSEHub(), sessions=_store(), messages=NS.MessageStore(),
                                  config=_CFG, runner=_StubRunner())
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def test_timing_off_by_default_then_switched_on(self):
        _req(self.port, "/global/health")
        code, out = _req(self.port, "/membrane/diag/timing")
        self.assertEqual(code, 200)
        self.assertFalse(out["on"])
        self.assertEqual(out["routes"], {})
        code, out = _req(self.port, "/membrane/diag/timing", "POST", {"on": True, "slow_ms": 0})
        self.assertEqual(code, 200)
        self.assertTrue(out["on"])
        self.assertEqual(out["slow_ms"], 0)
        _req(self.port, "/global/health?probe=1")
        _req(self.port, "/global/health")
        code, out = _req(self.port, "/membrane/diag/timing")
        row = out["routes"][f"{self.port} GET /global/health"]
        self.assertEqual(row["count"], 2)
        self.assertEqual(out["recent"][-1]["status"], 200)
        self.assertTrue(any("/global/health" in ln for ln in self.lines))   # slow_ms=0: every line
        _req(self.port, "/membrane/diag/timing", "POST", {"on": False})
        _req(self.port, "/global/health")
        code, out = _req(self.port, "/membrane/diag/timing")
        self.assertFalse(out["on"])
        self.assertEqual(out["routes"][f"{self.port} GET /global/health"]["count"], 2)

    def test_status_is_recorded(self):
        D.set_timing(True)
        _req(self.port, "/no/such/route")
        out = D.summary()
        self.assertEqual(out["recent"][-1]["status"], 404)

    def test_profile_endpoint(self):
        code, out = _req(self.port, "/membrane/diag/profile?seconds=0.3", "POST")
        self.assertEqual(code, 200, out)
        self.assertTrue(out["running"])
        code2, _ = _req(self.port, "/membrane/diag/profile?seconds=0.3", "POST")
        self.assertEqual(code2, 409)
        self.assertTrue(D.profile_wait(10))
        code, out = _req(self.port, "/membrane/diag/profile")
        self.assertEqual(code, 200)
        self.assertFalse(out["running"])
        self.assertTrue(os.path.exists(out["file"]))
        self.assertIn("threads", out)

    def test_localhost_only(self):
        far = {"X-Forwarded-For": "192.168.1.50"}      # a forwarded caller is off-box
        code, _ = _req(self.port, "/membrane/diag/timing", headers=far)
        self.assertEqual(code, 403)
        code, _ = _req(self.port, "/membrane/diag/profile", headers=far)
        self.assertEqual(code, 403)
        code, _ = _req(self.port, "/membrane/diag/timing", "POST", {"on": True}, headers=far)
        self.assertIn(code, (401, 403))
        self.assertFalse(D.timing_on())
        code, _ = _req(self.port, "/membrane/diag/profile?seconds=0.3", "POST", headers=far)
        self.assertIn(code, (401, 403))
        self.assertFalse(D.profile_status()["running"])


class TestProxyEndpoints(_DiagCase):
    """:11500 -- the /v1 proxy handler."""

    def setUp(self):
        super().setUp()
        from membrane import lanes as L
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), L.MembraneOpenAIHandler)
        self.srv.backend = "http://127.0.0.1:9"
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def test_timing_and_switch(self):
        code, out = _req(self.port, "/membrane/diag/timing")
        self.assertEqual(code, 200)
        self.assertFalse(out["on"])
        code, out = _req(self.port, "/membrane/diag/timing", "POST", {"on": True})
        self.assertEqual(code, 200)
        self.assertTrue(out["on"])
        _req(self.port, "/membrane/gate-stages")
        code, out = _req(self.port, "/membrane/diag/timing")
        row = out["routes"][f"{self.port} GET /membrane/gate-stages"]
        self.assertEqual(row["count"], 1)
        self.assertGreaterEqual(row["max"], 0)

    def test_profile_and_localhost_only(self):
        code, out = _req(self.port, "/membrane/diag/profile?seconds=0.2", "POST")
        self.assertEqual(code, 200, out)
        self.assertTrue(D.profile_wait(10))
        code, out = _req(self.port, "/membrane/diag/profile")
        self.assertEqual(code, 200)
        self.assertGreater(out["samples"], 0)
        far = {"X-Forwarded-For": "192.168.1.50"}
        self.assertEqual(_req(self.port, "/membrane/diag/timing", headers=far)[0], 403)
        self.assertIn(_req(self.port, "/membrane/diag/timing", "POST", {"on": True}, headers=far)[0],
                      (401, 403))
        self.assertFalse(D.timing_on())


if __name__ == "__main__":
    unittest.main()
