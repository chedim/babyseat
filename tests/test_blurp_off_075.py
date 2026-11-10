"""0.7.5 (the operator, 2026-11-10): "disable blurps for 0.7.5 and we'll rewrite them later" + "remove their ui from
ui". Blurp is off unless MEMBRANE_BLURP=1: its process doesn't boot, the page's conf says so, and the page hides
every Blurp part."""
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import self_super as SUP  # noqa: E402
from membrane import ui_serve as UI  # noqa: E402

HOME = (Path(__file__).resolve().parent.parent / "membrane" / "ui" / "home.js").read_text()


def test_blurp_is_off_by_default_and_the_switch_turns_it_on():
    with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "MEMBRANE_BLURP"}, clear=True):
        assert SUP.blurp_on() is False
        assert UI.conf()["blurp"] is False
    with mock.patch.dict(os.environ, {"MEMBRANE_BLURP": "1"}):
        assert SUP.blurp_on() is True
        assert UI.conf()["blurp"] is True


def test_the_off_seat_boots_no_blurp_process():
    with mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "MEMBRANE_BLURP"}, clear=True), \
            mock.patch.object(SUP, "Supervisor", side_effect=AssertionError("Blurp's process booted while off")), \
            mock.patch.object(SUP, "_SL", side_effect=AssertionError("Blurp's loop started while off")):
        assert SUP.boot(object(), "http://127.0.0.1:4096") is None


def test_the_page_hides_every_blurp_part_when_off():
    for cls in ("babybtn", "selfpanel", "toydock", "bbody", "selfstat", "rungbell", "celebrate"):
        assert f".noblurp .{cls}" in HOME, cls
    assert "window.MEMBRANE_BLURP === false" in HOME
