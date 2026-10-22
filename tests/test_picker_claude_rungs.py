"""The chat's model picker offers the claude-agent rungs the seat serves on
/v1/models (owned_by membrane->claude-agent), beside the Ollama models; other
cloud providers' catalog rows and the router's lanes stay out of it."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"
NODE = unittest.skipUnless(shutil.which("node"), "node not installed")

ROWS = [{"id": "qwen3:14b", "owned_by": "membrane->ollama"},
        {"id": "@explore", "owned_by": "membrane->ollama"},
        {"id": "autoselect", "owned_by": "membrane-router"},
        {"id": "x-ai/grok-4.20-multi-agent", "owned_by": "membrane->openrouter"},
        {"id": "claude-agent:haiku", "owned_by": "membrane->claude-agent"},
        {"id": "claude-agent:sonnet", "owned_by": "membrane->claude-agent"}]


def _picker(rows):
    js = "const L=require(%r); process.stdout.write(JSON.stringify(L.ollamaModels(%s)))" % (
        str(UI / "panel.js"), json.dumps(rows))
    return json.loads(subprocess.check_output(["node", "-e", js]))


@NODE
class TestPickerOffersClaudeRungs(unittest.TestCase):
    def test_claude_agent_rungs_are_offered_after_the_ollama_models(self):
        self.assertEqual(_picker(ROWS), ["qwen3:14b", "claude-agent:haiku", "claude-agent:sonnet"])

    def test_without_claude_rungs_the_list_is_the_ollama_models(self):
        self.assertEqual(_picker(ROWS[:4]), ["qwen3:14b"])
