"""The UI pack (docs/ui-pack-design.md): native/packs/uifeedback.c1f, headless, on the field engine."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_fields_live_994 as T  # noqa: E402  (its `seat` fixture and helpers)
from test_fields_live_994 import MV, settle, write, p  # noqa: E402,F401
from membrane import fields_live as FL  # noqa: E402
from membrane.entities import MEMBRANE_NS, QuantumEntity  # noqa: E402

seat = T.seat
PACK = Path(__file__).resolve().parent.parent / "native" / "packs" / "uifeedback.c1f"
R = MEMBRANE_NS + "reply:r1"
R2 = MEMBRANE_NS + "reply:r2"


def test_pack_file_is_fields_only_and_imports_goalgraph():
    t = PACK.read_text()
    assert t.startswith("PACK uifeedback 1") and "IMPORT goalgraph 1" in t
    assert "<script" not in t and "document." not in t and "innerHTML" not in t
    for name in ("verified", "quality", "superseded", "up", "down", "impact_shown", "feedback-note", "feedback-flag"):
        assert name in t, name


@pytest.fixture
def ui(seat):
    est = seat.entity_store
    est.upsert(QuantumEntity(eid=FL.PACK_NS + "uifeedback", cls=FL.PACK_CLASS,
                             properties={"name": "uifeedback", "c1f": PACK.read_text()}))
    est.upsert(QuantumEntity(eid=R, cls="Reply", properties={"receipts": 0, "unchecked": 0}))
    settle(seat)
    assert not p(seat, FL.PACK_NS + "uifeedback", "error")
    return seat


def test_quality_follows_receipts_and_unchecked_claims(ui):
    assert p(ui, R, "quality") == "unchecked"
    write(ui, R, "receipts", 2)
    assert p(ui, R, "verified") is True and p(ui, R, "quality") == "verified"
    write(ui, R, "unchecked", 1)
    assert p(ui, R, "verified") is False and p(ui, R, "quality") == "partial"


def test_feedback_counts_and_notes_on_the_reply(ui):
    est = ui.entity_store
    F1, F2 = MEMBRANE_NS + "feedback:f1", MEMBRANE_NS + "feedback:f2"
    est.upsert(QuantumEntity(eid=F1, cls="Feedback", properties={"reply": R}))
    est.upsert(QuantumEntity(eid=F2, cls="Feedback", properties={"reply": R}))
    settle(ui)
    write(ui, F1, "kind", "useful")
    write(ui, F2, "kind", "biased")
    assert p(ui, R, "up") == 1 and p(ui, R, "down") == 1 and p(ui, R, "reports") == 1
    assert p(ui, R, "last_feedback") == "biased" and p(ui, R, "flagged") is True


def test_retry_supersedes_the_old_reply(ui):
    ui.entity_store.upsert(QuantumEntity(eid=R2, cls="Reply", properties={"receipts": 1}))
    settle(ui)
    assert not p(ui, R, "superseded")
    write(ui, R2, "retry_of", R)
    assert p(ui, R, "superseded") is True


def test_decision_impact_and_tone(ui):
    est = ui.entity_store
    D, P = MEMBRANE_NS + "Decision:d1", MEMBRANE_NS + "prefs:me"
    est.upsert(QuantumEntity(eid=D, cls="Decision", properties={"status": "open"}))
    est.upsert(QuantumEntity(eid=P, cls="Prefs", properties={"tone": "loud"}))
    settle(ui)
    assert p(ui, D, "impact_shown") == "impact not stated"
    write(ui, D, "impact", "old UI is removed")
    assert p(ui, D, "impact_shown") == "old UI is removed"
    assert p(ui, P, "tone_level") == "normal"
    write(ui, P, "tone", "terse")
    assert p(ui, P, "tone_level") == "terse"
