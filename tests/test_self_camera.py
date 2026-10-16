"""#161: the home view's camera can follow self (◎ activity · seat · you · self)."""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "membrane" / "ui"


def test_self_is_a_follow_mode():
    home = (UI / "home.js").read_text()
    assert 'const FOLLOW_MODES = ["activity", "seat", "you", "self"];' in home


def test_the_camera_follows_the_amoeba_as_it_moves():
    home = (UI / "home.js").read_text()
    assert 'this._mode === "self"' in home
    assert "selfPos()" in home
    assert "following self" in home
    assert "self is not moving yet" in home


def test_the_viewer_says_where_self_is():
    js = (UI / "field3d.js").read_text()
    assert "  selfPos() {" in js
