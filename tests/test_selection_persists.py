"""THE SEAT'S CHOSEN GOALS SURVIVE A RESTART (2026-10-08, the operator: "why
are you resetting its goals with every restart? that's not nice", red first).

The goals themselves always persisted (store docs). The seat's CHOICE did
not: the `intend` pins (the priority bonus on a goal it committed to) and
its drops (suppressed goals) lived in RAM -- every restart re-ranked the
active set from scratch, commitments lost their weight, dropped goals came
back. Pinned: both ride <field dir>/goal-selection.json, reloaded at attach.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import CLASS_GOAL, EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402
from membrane.intent import IntentBoard  # noqa: E402


def _est(d):
    est = EntityStore(path=str(Path(d) / "e.json"), shared=False)
    for slug in ("a", "b", "c", "d"):
        est.docs[f"{MEMBRANE_NS}goal:{slug}"] = QuantumEntity(
            eid=f"{MEMBRANE_NS}goal:{slug}", cls=CLASS_GOAL,
            properties={"name": slug, "status": "open", "born": time.time()},
            position=[1.0, 0.0, 0.0])
    return est


class TestSelectionPersists(unittest.TestCase):
    def test_pins_and_drops_survive_a_new_board(self):
        d = tempfile.mkdtemp()
        est = _est(d)
        b1 = IntentBoard(Path(d) / "intents.json")
        b1.attach_entity_store(est)
        b1.selection.pin(f"{MEMBRANE_NS}goal:c", 0.5)
        b1.selection.suppress(f"{MEMBRANE_NS}goal:a")
        b2 = IntentBoard(Path(d) / "intents.json")          # the restart
        b2.attach_entity_store(est)
        self.assertEqual(b2.selection.pinned.get(f"{MEMBRANE_NS}goal:c"), 0.5)
        self.assertIn(f"{MEMBRANE_NS}goal:a", b2.selection.suppressed)
        active = [i.eid for i in b2.selection.open_intents(time.time())]
        self.assertIn(f"{MEMBRANE_NS}goal:c", active)
        self.assertNotIn(f"{MEMBRANE_NS}goal:a", active)

    def test_a_closed_goal_drops_out_of_the_file(self):
        d = tempfile.mkdtemp()
        est = _est(d)
        b = IntentBoard(Path(d) / "intents.json")
        b.attach_entity_store(est)
        b.selection.pin(f"{MEMBRANE_NS}goal:b", 0.5)
        est.docs[f"{MEMBRANE_NS}goal:b"].properties["status"] = "fulfilled"
        b2 = IntentBoard(Path(d) / "intents.json")
        b2.attach_entity_store(est)
        self.assertNotIn(f"{MEMBRANE_NS}goal:b", b2.selection.pinned)


if __name__ == "__main__":
    unittest.main()
