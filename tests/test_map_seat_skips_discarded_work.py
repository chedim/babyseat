"""Live 2026-11-11: the first response of a map-mode seat took 23-64 s; 17 s of the gate were work whose result the
map rewrite throws away (the answer-event gate 7.5 s, two sibling-seat asks at a 5 s timeout each)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import lanes  # noqa: E402


def test_a_map_seat_skips_the_gate_and_the_peer_asks(monkeypatch):
    monkeypatch.setenv("MEMBRANE_EXP_MOMENTUM_MAP", "1")
    assert lanes._runs_answer_gate() is False and lanes._asks_peers() is False


def test_every_other_seat_keeps_them(monkeypatch):
    monkeypatch.delenv("MEMBRANE_EXP_MOMENTUM_MAP", raising=False)
    assert lanes._runs_answer_gate() is True and lanes._asks_peers() is True
