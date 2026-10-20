"""#301: docs/sleep.md exists, is in the README's docs map and linked from the
growth guide, and the files it points to are real."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "sleep.md"


def test_the_sleep_doc_is_on_the_map():
    assert DOC.exists()
    assert "](docs/sleep.md)" in (ROOT / "README.md").read_text()
    assert "](sleep.md)" in (ROOT / "docs" / "selfgrowth.md").read_text()


def test_every_relative_link_in_it_resolves():
    for target in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", DOC.read_text()):
        assert (DOC.parent / target).exists(), target
