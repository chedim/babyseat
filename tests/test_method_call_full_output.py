"""THE CLIPPED METHOD (follow-up to the clipped shell, #506; red first).

`method_call`'s reply carried `head[:600]` of the method's run, as
entity_link's did. Like entity_link now, the reply carries the run's FULL
output up to mcp_server.LINK_OUTPUT_CAP (30000 chars); past it the head
(2/3) and the tail (1/3) around the cut marker, the total size and the
re-run hint. What is stored (the method's run_head/run_tail residue, the
object's call ring) stays small; the local models' toolset path
(server.mcp_executor -> LOCAL_TOOL_IMPLS) keeps the 600-char head.
"""

import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import night as N  # noqa: E402
from membrane import streams as ST  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore, CLASS_COMPONENT  # noqa: E402


def _printer(head: int, mid: int = 0, tail: int = 0, rc: int = 0) -> str:
    return (f"{sys.executable} -c \"import sys; sys.stdout.write("
            f"'H'*{head}+'M'*{mid}+'T'*{tail}); sys.exit({rc})\"")


class _World:
    """A real object + method behind a fake HTTP hop (the route's JSON round trip)."""

    def __init__(self, command: str):
        self.command = command

    def __enter__(self):
        f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
        est = EntityStore(path=None, shared=False)
        N.seed_executor_entity(est)
        srv = type("S", (), {"efield": f, "entity_store": est, "membrane": None,
                             "capturer": None})()
        obj = est.docs[est.add(cls=CLASS_COMPONENT, name="printer box", summary="x")["eid"]]
        d = N.method_define(srv, est, f, None, obj.eid, "dump", self.command)
        assert d["ok"], d
        self.est, self.obj = est, obj

        def fake_http(port, method, path, body=None):
            assert path == "/membrane/method/call"
            a = body.get("args")
            res = N.method_call(srv, est, f, None, body.get("object") or "",
                                body.get("method") or "", a if isinstance(a, (dict, list)) else {})
            return json.loads(json.dumps(res))      # what the route's _json_out carries
        self._orig = M._http
        M._http = fake_http
        return self

    def __exit__(self, *a):
        M._http = self._orig

    def call(self, impls=None) -> str:
        fn = (impls or M.TOOL_IMPLS)["method_call"]
        return fn({"object": self.obj.eid, "method": "dump"}, 0)

    def stored_sizes(self) -> list[tuple[int, int]]:
        return [(len(str(p.get("run_head") or "")), len(str(p.get("run_tail") or "")))
                for p in ((d.properties or {}) for d in self.est.docs.values())
                if "run_head" in p]


class TestMethodCallFullOutput(unittest.TestCase):
    def test_10k_output_comes_back_whole(self):
        with _World(_printer(10000)) as w:
            reply = w.call()
            sizes = w.stored_sizes()
        self.assertIn("H" * 10000, reply)
        self.assertNotIn("chars cut", reply)
        self.assertTrue(sizes)
        for h, t in sizes:                              # the field stays small
            self.assertLessEqual(h, ST.RESULT_HEAD)
            self.assertLessEqual(t, ST.RESULT_TAIL)

    def test_50k_output_is_head_and_tail_with_the_cut_marker(self):
        cap = M.LINK_OUTPUT_CAP
        with _World(_printer(30000, 5000, 15000)) as w:
            reply = w.call()
        self.assertIn("H" * (cap * 2 // 3), reply)
        self.assertNotIn("H" * (cap * 2 // 3 + 1), reply)
        self.assertIn("T" * (cap // 3), reply)
        self.assertNotIn("T" * (cap // 3 + 1), reply)
        self.assertNotIn("MMMMM", reply)
        self.assertIn("20000 chars cut", reply)
        self.assertIn("50000 chars", reply)
        self.assertIn("sed -n", reply)
        self.assertLess(len(reply), cap + 1500)

    def test_a_failing_call_shows_its_output_too(self):
        with _World(_printer(5000, rc=1)) as w:
            reply = w.call()
        self.assertIn("rc=1", reply)
        self.assertIn("H" * 5000, reply)

    def test_local_models_keep_their_small_head(self):
        with _World(_printer(10000)) as w:
            reply = w.call(M.LOCAL_TOOL_IMPLS)
        self.assertIn("H" * M.LOCAL_LINK_HEAD, reply)
        self.assertNotIn("H" * (M.LOCAL_LINK_HEAD + 1), reply)

    def test_the_local_executor_uses_the_small_head(self):
        from membrane.server import mcp_executor
        with _World(_printer(10000)) as w:
            reply = mcp_executor(0)("method_call", {"object": w.obj.eid, "method": "dump"})
        self.assertNotIn("H" * (M.LOCAL_LINK_HEAD + 1), reply)


if __name__ == "__main__":
    unittest.main()
