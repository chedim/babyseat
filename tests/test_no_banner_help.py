"""--no-banner's help said "recall stays", but since 0.7.4 recall IS the banner: in the conversation injection mode
the flag skips conv_banner.build, so the turn gets no recall at all. The token bench ran with it and its memory arm
remembered nothing (2026-11-11)."""
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "membrane" / "opencode.py").read_text()


def test_the_help_does_not_promise_recall():
    i = SRC.index('"--no-banner"')
    help_text = SRC[i:SRC.index("set_defaults(no_banner", i)]
    assert "recall stays" not in help_text
    assert "recall" in help_text and "off" in help_text
