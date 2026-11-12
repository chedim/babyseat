"""#470: the board's chat write. The board's routes (read, answer, drop, chat, goals) are the door's now
(packs/door_decisions.c1f, specs in tests/specs/door_decisions.json); what stays here is that the page records a card's
chat through the decisions route and not through entity/edit."""
from pathlib import Path


def test_the_board_records_its_chat_through_the_serve_route():
    ui = Path(__file__).resolve().parent.parent / "membrane" / "ui"
    src = (ui / "decisions.js").read_text() + (ui / "home.js").read_text()
    assert '"/membrane/decision/chat"' in src
    assert '"/membrane/entity/edit"' not in src
