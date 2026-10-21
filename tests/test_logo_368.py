"""#368: the logo -- the word babyseat over the field sphere -- ships in the
repo in three sizes with the pages that render them, and heads the README."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "docs" / "logo"
SIZES = {"logo-1920x1080.png": (1920, 1080), "logo-1280x640.png": (1280, 640),
         "logo-square-1024.png": (1024, 1024)}


def _png_size(p: Path):
    b = p.read_bytes()[:24]
    assert b[:8] == b"\x89PNG\r\n\x1a\n", p
    return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")


def test_every_size_is_a_png_of_its_size():
    for name, wh in SIZES.items():
        assert _png_size(LOGO / name) == wh, name


def test_the_pages_that_render_them_are_kept():
    for name in ("render-banner.html", "render-card.html", "render-square.html"):
        assert "babyseat" in (LOGO / name).read_text()
    assert "--screenshot" in (LOGO / "README.md").read_text()      # how to redo them


def test_the_logo_heads_the_readme():
    head = (ROOT / "README.md").read_text().splitlines()[:4]
    assert any(re.search(r"!\[babyseat\]\(docs/logo/logo-1280x640\.png\)", l) for l in head)
