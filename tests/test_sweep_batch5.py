"""THE SWEEP, BATCH 5 (2026-10-11): the store's concurrency -- a write landing
during a save is no longer forgotten; the save iterates a copy of the docs."""

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, MEMBRANE_NS, QuantumEntity  # noqa: E402


class TestTheDirtyMark(unittest.TestCase):
    def test_a_write_during_the_save_stays_marked(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        est.SAVE_DEBOUNCE_S = 60
        real = est.save

        def save_with_a_write_in_the_middle():
            e = QuantumEntity(eid=MEMBRANE_NS + "Component:late", cls="Component", properties={"name": "late"})
            est.docs[e.eid] = e
            est.save_soon()                         # another thread's upsert, mid-save
            real()
        est.save = save_with_a_write_in_the_middle
        est._dirty = True
        est._flush_save()
        self.assertTrue(est._dirty)                # the late write is still owed a save
        est._cancel_save_timer()

    def test_the_save_survives_docs_added_meanwhile(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        for i in range(300):
            e = QuantumEntity(eid=MEMBRANE_NS + f"Component:c{i}", cls="Component", properties={"name": f"c{i}"})
            est.docs[e.eid] = e
        stop = threading.Event()

        def adder():
            for i in range(400):                    # bounded: the save must not race an endless store
                if stop.is_set():
                    break
                e = QuantumEntity(eid=MEMBRANE_NS + f"Component:x{i}", cls="Component", properties={"name": "x"})
                est.docs[e.eid] = e
        t = threading.Thread(target=adder, daemon=True)
        t.start()
        try:
            for _ in range(3):
                est.save()
                self.assertTrue(getattr(est, "_side_saved", True))
        finally:
            stop.set()
            t.join(2)


if __name__ == "__main__":
    unittest.main()
