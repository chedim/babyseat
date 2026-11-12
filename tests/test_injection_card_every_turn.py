"""Live 2026-11-11: the injection card (the banner, on a map seat) showed once and never again: _inject_card skipped
any injection equal to the chat's last one, and a banner is the same text every turn. The first round of every turn
shows its card; a later round only when it differs."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import server as S  # noqa: E402


def _runner():
    seen = []
    return SimpleNamespace(hub=SimpleNamespace(publish=lambda ev: seen.append(ev)), _last_inj={}), seen


def _card(runner, text, rounds):
    S.TurnRunner._inject_card(runner, "ses_a", {"injection": text}, rounds, lambda p: p)


def test_a_round_delivered_twice_shows_one_card_and_a_new_turn_shows_its_own(monkeypatch):
    r, seen = _runner()
    _card(r, "BANNER", 1)
    _card(r, "BANNER", 1)           # the same round again (the stream and the final response both carry it)
    assert len(seen) == 1
    r._last_inj.pop("ses_a", None)  # what the turn loop does at the start of the next turn
    _card(r, "BANNER", 1)
    assert len(seen) == 2


def test_the_turn_loop_resets_the_record_at_its_start():
    src = (Path(S.__file__)).read_text()
    assert "_last_inj\", None) or {}).pop(sid, None)" in src
