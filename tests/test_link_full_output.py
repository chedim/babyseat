"""THE CLIPPED SHELL (operator report 2026-10-24, red first).

Memo (the claude-agent rung) runs shell commands through the MCP tool
`entity_link`, and every command's reply carried only `head[:600]` -- a
`cat`, a `sed -n` or a test run showed its first ~600 chars ("Output is
clipped"). Like Claude Code's Bash tool, the reply now carries the
command's FULL output up to LINK_OUTPUT_CAP (30000 chars); past it the
head (2/3) and the tail (1/3) with a cut marker, the total size and how
to read the rest. What is STORED on the command entity stays the short
run_head/run_tail residue. The local models' toolset path (server.py's
mcp_executor) keeps its small 600-char head: their context is small.
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
from membrane.entities import EntityStore  # noqa: E402


def _printer(head: int, mid: int = 0, tail: int = 0) -> str:
    """A command printing `head` H's, `mid` M's and `tail` T's (no newline)."""
    return (f"{sys.executable} -c \"import sys; sys.stdout.write("
            f"'H'*{head}+'M'*{mid}+'T'*{tail})\"")


class _Srv:
    def __init__(self, f, est):
        self.efield, self.entity_store, self.membrane, self.capturer = f, est, None, None


class _World:
    """The real link lane behind a fake HTTP hop (the route's JSON round trip)."""

    def __enter__(self):
        self.f = EntityField(MemBackend(), FakeEmbedder(), "seat@t")
        self.est = EntityStore(path=None, shared=False)
        N.seed_executor_entity(self.est)
        srv = _Srv(self.f, self.est)

        def fake_http(port, method, path, body=None):
            assert path == "/membrane/entity/link"
            res = N.entity_link(srv, self.est, self.f, None, body.get("executor") or "",
                                body.get("commands"), create=True)
            return json.loads(json.dumps(res))      # what the route's _json_out carries
        self._orig = M._http
        M._http = fake_http
        return self

    def __exit__(self, *a):
        M._http = self._orig

    def stored(self, cmd: str) -> dict:
        for d in self.est.docs.values():
            p = d.properties or {}
            if p.get("command") == cmd and "run_head" in p:
                return p
        raise AssertionError(f"no stored residue for {cmd!r}")


class TestEntityLinkFullOutput(unittest.TestCase):
    def test_10k_output_comes_back_whole(self):
        cmd = _printer(10000)
        with _World() as w:
            reply = M.tool_entity_link({"commands": [cmd]}, 0)
            self.assertIn("H" * 10000, reply)
            self.assertNotIn("chars cut", reply)
            p = w.stored(cmd)
        # THE FIELD STAYS SMALL: the entity keeps its short residue
        self.assertLessEqual(len(p["run_head"]), ST.RESULT_HEAD)
        self.assertLessEqual(len(p["run_tail"]), ST.RESULT_TAIL)
        self.assertNotIn("run_output", p)

    def test_50k_output_is_head_and_tail_with_the_cut_marker(self):
        cap = M.LINK_OUTPUT_CAP
        self.assertEqual(cap, 30000)
        cmd = _printer(30000, 5000, 15000)            # 50000 chars
        with _World() as w:
            reply = M.tool_entity_link({"commands": [cmd]}, 0)
            p = w.stored(cmd)
        # the head 2/3 and the tail 1/3 of the cap, nothing past it
        self.assertIn("H" * (cap * 2 // 3), reply)
        self.assertNotIn("H" * (cap * 2 // 3 + 1), reply)
        self.assertIn("T" * (cap // 3), reply)
        self.assertNotIn("T" * (cap // 3 + 1), reply)
        self.assertNotIn("MMMMM", reply)              # the middle is the cut
        self.assertIn("20000 chars cut", reply)
        self.assertIn("50000 chars", reply)           # the total size
        self.assertIn("sed -n", reply)                # how to read the rest
        self.assertLess(len(reply), cap + 1000)
        self.assertLessEqual(len(p["run_head"]), ST.RESULT_HEAD)
        self.assertLessEqual(len(p["run_tail"]), ST.RESULT_TAIL)

    def test_a_failing_run_shows_its_output_too(self):
        """A red test run (rc != 0) is where the output matters most."""
        cmd = (f"{sys.executable} -c \"import sys; sys.stdout.write('F'*5000); "
               f"sys.exit(1)\"")
        with _World():
            reply = M.tool_entity_link({"commands": [cmd]}, 0)
        self.assertIn("FAILED", reply)
        self.assertIn("F" * 5000, reply)

    def test_the_route_json_carries_a_bounded_output(self):
        """The run capture never ships megabytes over the route: the run's
        `output` is already cut to the cap."""
        cmd = _printer(200000)
        with _World() as w:
            res = N.entity_link(_Srv(w.f, w.est), w.est, w.f, None, "", [cmd], create=True)
        run = res["runs"][0]
        self.assertEqual(run["output_len"], 200000)
        self.assertLess(len(run["output"]), M.LINK_OUTPUT_CAP + 200)

    def test_local_models_keep_their_small_head(self):
        """The toolset path (server.py mcp_executor -> entity_link) keeps the
        600-char head the local models always had."""
        from membrane.server import mcp_executor
        cmd = _printer(10000)
        with _World():
            reply = mcp_executor(0)("entity_link", {"commands": [cmd]})
        self.assertIn("H" * M.LOCAL_LINK_HEAD, reply)
        self.assertNotIn("H" * (M.LOCAL_LINK_HEAD + 1), reply)
        self.assertEqual(M.LOCAL_LINK_HEAD, 600)


if __name__ == "__main__":
    unittest.main()
