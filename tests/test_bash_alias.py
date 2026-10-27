"""`bash` IS code_run UNDER THE NAME MODELS KNOW (the operator, 2026-10-27: "make
bash alias to code_run instead of renaming").

Claude Code and opencode both call their shell tool bash, with {command,
timeout (ms), description}. Pinned: the MCP list carries a `bash` tool of that
shape; calling it is code_run with lang='sh' (the timeout in seconds); and it
sits in exactly the lanes code_run sits in.
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import jobs as J  # noqa: E402
from membrane import mcp_server as M  # noqa: E402
from membrane import server as S  # noqa: E402


class TestBashIsCodeRun(unittest.TestCase):
    def test_the_list_carries_bash_with_the_familiar_shape(self):
        t = next(t for t in M.TOOLS if t["name"] == "bash")
        props = t["inputSchema"]["properties"]
        self.assertEqual(set(props), {"command", "timeout", "description"})
        self.assertEqual(t["inputSchema"]["required"], ["command"])

    def test_a_bash_call_is_a_code_run(self):
        sent = []
        real = M._http
        M._http = lambda port, method, path, body=None, timeout=None: sent.append((path, body)) or {
            "ok": True, "rc": 0, "root": "membrane::node:sh:x", "result": "membrane::result:x:1",
            "executor": "membrane::tool:executor", "text": "ls", "head": "a", "output": "a"}
        try:
            for impls in (M.TOOL_IMPLS, M.LOCAL_TOOL_IMPLS):
                out = impls["bash"]({"command": "ls", "timeout": 5000, "description": "list"}, 1)
                self.assertIn("result membrane::result:x:1", out)
        finally:
            M._http = real
        self.assertEqual(sent[0], ("/membrane/code/run", {"code": "ls", "lang": "sh", "timeout": "5.0"}))

    def test_a_local_model_calling_bash_reaches_code_run_in_its_lanes(self):
        from membrane import toolset as T
        name, args = T.resolve("bash", {"command": "ls", "timeout": 5000})
        self.assertEqual((name, args), ("code_run", {"code": "ls", "lang": "sh", "timeout": 5.0}))
        self.assertIsNone(S._lane_refusal(name, "operator"))
        self.assertIsNotNone(S._lane_refusal(name, "plan"))
        self.assertIn(name, J.RUN_TOOLS)

if __name__ == "__main__":
    unittest.main()
