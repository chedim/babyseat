"""THE CLAUDE-AGENT RUNG LOADS NO AUTO-MEMORY.

setting_sources=[] keeps settings files and CLAUDE.md out, but the CLI's
auto-memory is keyed to the working directory: a seat started in a project
that a Claude Code session also works in got that session's MEMORY.md
injected into every agent turn as "instructions that OVERRIDE any default
behavior" (~16K chars per step, captured 2026-11-02). The rung's env turns
auto-memory off for the CLI subprocess, whatever env the caller passes.
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402

HAVE_SDK = importlib.util.find_spec("claude_agent_sdk") is not None


@unittest.skipUnless(HAVE_SDK, "claude-agent-sdk (optional extra) not installed")
class TestNoAutoMemory(unittest.TestCase):
    def _env(self, env):
        import claude_agent_sdk as sdk
        opts = CA.build_options(sdk, "sys", "claude-agent:haiku", stream=False,
                                port=9, env=env)
        return opts.env

    def test_auto_memory_off_with_a_given_env(self):
        self.assertEqual(self._env({"ANTHROPIC_API_KEY": "k"}).get(
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY"), "1")

    def test_auto_memory_off_with_an_empty_env(self):
        self.assertEqual(self._env({}).get("CLAUDE_CODE_DISABLE_AUTO_MEMORY"), "1")

    def test_the_callers_env_is_kept(self):
        self.assertEqual(self._env({"ANTHROPIC_API_KEY": "k"}).get("ANTHROPIC_API_KEY"), "k")


if __name__ == "__main__":
    unittest.main()
