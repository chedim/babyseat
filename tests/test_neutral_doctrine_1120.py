"""#1120 (operator 2026-11-11): the goal doctrine, help and planner/worker texts are domain-neutral -- babyseat is a
general tool (a boat, trips, a home, research); code is one domain among them. Same rules, neutral words."""
import os
import sys
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import claude_agent as CA  # noqa: E402
from membrane import goalgraph as GG  # noqa: E402
from membrane import speech as SP  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEXTS = {"doctrine": (ROOT / "membrane" / "doctrine.md").read_text(),
         "help goals": (ROOT / "membrane" / "help" / "goals.md").read_text(),
         "planner": GG.PLANNER_TEXT, "worker": GG.WORKER_TEXT, "act hint": SP.ACT_HINT,
         "planner system": getattr(GG, "PLANNER_SYSTEM", "")}
CODE_ONLY = ("issue link", "issue to build", "PR head", "its CI", "CI going green", "CI takes")


def test_no_software_only_examples():
    for name, text in TEXTS.items():
        for w in CODE_ONLY:
            assert w not in text, f"{name}: {w!r}"
    assert "issue link" not in CA.TOOLS_NOTE


def test_the_examples_mix_domains():
    t = TEXTS["doctrine"] + TEXTS["help goals"]
    assert "trip" in t and "repair" in t and "research" in t
