"""The /membrane/delegate endpoint: model-pointed work, router-disposed.

The caller never names the target; the operator's chat pick is the ceiling;
a model cannot mint authority (its "ceiling" field is ignored); the explore
lane is the only overridable request; outcomes feed the router's measured
record."""

import json
import math
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from membrane import router as R
from membrane.router import Registry


def _vec(seed: float) -> np.ndarray:
    v = np.asarray([math.cos(seed), math.sin(seed), 0.3], dtype=float)
    return v / np.linalg.norm(v)


class _Reg(Registry):
    def __init__(self, *a, **kw):
        self._canned = kw.pop("tags", None)
        super().__init__(*a, **kw)

    def refresh(self):
        tags = self._canned
        if not tags:
            return
        self.resident = {m.get("name", "") for m in
                         (tags.get("_ps") or {}).get("models", [])}
        for m in tags.get("models", []) or []:
            name = m.get("name") or ""
            if not name:
                continue
            rec = self.records.setdefault(name, R.ModelRecord(name=name))
            rec.kind = ("cloud" if (bool(m.get("remote_host"))
                                    or not m.get("size")) else "local")
            rec.size_bytes = int(m.get("size") or 0)
            rec.caps = list(m.get("capabilities") or [])


TAGS = {"models": [
    {"name": "glm-5.3-flash:cloud", "size": 0,
     "remote_host": "https://api.z.ai",
     "capabilities": ["completion", "tools"]},
    {"name": "deepseek-r1:8b", "size": 4 * 2**30,
     "capabilities": ["completion", "tools"]},
]}


class _StubBackend(BaseHTTPRequestHandler):
    """A fake Ollama: records which model was asked, answers tersely."""

    asked = []

    def do_POST(self):
        body = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0))))
        _StubBackend.asked.append(body.get("model"))
        out = json.dumps({"choices": [{"message": {
            "role": "assistant", "content": "stub-answer"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class TestDelegate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from membrane import opencode as OC
        backend = ThreadingHTTPServer(("127.0.0.1", 0), _StubBackend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        cls.backend = backend

        srv = ThreadingHTTPServer(("127.0.0.1", 0), OC.MembraneOpenAIHandler)
        srv.backend = f"http://127.0.0.1:{backend.server_address[1]}"
        srv.membrane = type("M", (), {})()
        srv.membrane.embedder = None          # no embeddings in this test
        srv.membrane.store = None
        router = _Reg(srv.backend, tags=TAGS, explore=0.0)
        router.records["deepseek-r1:8b"].imprint = [list(_vec(1.0))]
        srv.router = router
        srv.op_ceiling = "glm-5.3-flash:cloud"   # the operator picked cloud
        srv._delegate_busy = False
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.srv = srv
        cls.port = srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.backend.shutdown()

    def _post(self, payload):
        rq = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/membrane/delegate",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=30) as r:
            return json.loads(r.read())

    def test_operator_ceiling_is_law(self):
        # the request tries to mint a pricier ceiling; the operator's pick
        # (glm cloud) answers instead
        out = self._post({"task": "sum 2+2",
                          "ceiling": "claude-opus:cloud"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "glm-5.3-flash:cloud")

    def test_explore_lane_requested(self):
        out = self._post({"task": "sum 2+2", "lane": "explore"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["model"], "deepseek-r1:8b")
        self.assertTrue(out["explored"])

    def test_empty_task_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post({"task": ""})
        self.assertEqual(cm.exception.code, 400)

    def test_no_nested_delegation(self):
        # the depth-1 guard: a second delegate while one is busy -> 429
        self.srv._delegate_busy = True
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._post({"task": "x"})
            self.assertEqual(cm.exception.code, 429)
        finally:
            self.srv._delegate_busy = False

    def test_outcome_recorded(self):
        before = self.srv.router.records["glm-5.3-flash:cloud"].trials
        self._post({"task": "compute the sum"})
        self.assertEqual(
            self.srv.router.records["glm-5.3-flash:cloud"].trials, before + 1)


if __name__ == "__main__":
    unittest.main()