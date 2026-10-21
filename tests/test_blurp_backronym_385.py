"""#385: the guide says what Blurp stands for -- a Baby sLURP."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_guide_spells_the_backronym():
    head = (ROOT / "docs" / "selfgrowth.md").read_text()[:1500]
    assert "**B**aby s**LURP**" in head
