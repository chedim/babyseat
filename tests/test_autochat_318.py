"""#318: the babble role and the LLM switch are gone.

Auto-chat itself went in #348 (the LLM acts through its own field scripts,
#338; its ▶ became the seat's pause -- tests/test_nerve_pause_348.py), and
with it this file's auto-chat tests. Pinned here still: no babble role, role
switch or LLM switch is left in the code or the UI.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "membrane" / "ui"
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

from membrane import voice as V  # noqa: E402


class TestTheBabbleRoleIsGone(unittest.TestCase):
    def test_no_role_plumbing_in_the_voice(self):
        for name in ("ROLES", "role", "role_set", "babble_move", "frame_move", "frame", "frame_explore"):
            self.assertFalse(hasattr(V, name), name)

    def test_no_babble_hooks_in_the_server(self):
        src = (ROOT / "membrane" / "server.py").read_text()
        for s in ("babble_move", "_babble_threads", "role_set"):
            self.assertNotIn(s, src)

    def test_no_role_or_llm_switch_in_the_ui(self):
        panel = (UI / "panel.js").read_text()
        for s in ("rolebtn", "llmbtn", "_llmOn", "membrane_only", "roleNext", "roleView", "_toggleRole",
                  "membrane babbles", "membrane predicts"):
            self.assertNotIn(s, panel)
        home = (UI / "home.js").read_text()
        self.assertNotIn("a.role", home)
