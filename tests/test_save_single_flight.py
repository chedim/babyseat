"""ONE ENTITY-STORE SAVE AT A TIME (2026-11-03: a restart's drain found 150 save threads waiting
on the database thread, ~25 minutes of saves, and the 90 s teardown lease cut the seat before it
could re-exec). The debounce timer was cleared when a save STARTED, so while a slow save waited
(it is background work, behind the turns), every new write armed another timer and started
another save beside it. Now a write during a save only marks the store dirty; the running save
re-arms one timer when it ends."""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.entities import EntityStore, QuantumEntity  # noqa: E402


class SingleFlight(unittest.TestCase):
    def _store(self):
        est = EntityStore(path=str(Path(tempfile.mkdtemp()) / "e.json"), shared=False)
        est.SAVE_DEBOUNCE_S = 0.02
        state = {"now": 0, "max": 0, "n": 0}
        lock = threading.Lock()
        real = est.save

        def slow_save(*a, **k):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
                state["n"] += 1
            time.sleep(0.15)                    # slower than the debounce: the old pile-up
            try:
                return real(*a, **k)
            finally:
                with lock:
                    state["now"] -= 1
        est.save = slow_save
        return est, state

    def test_writes_during_a_slow_save_never_start_a_second_one(self):
        est, state = self._store()
        for i in range(40):
            est.upsert(QuantumEntity(eid=f"membrane::t:{i}", cls="Component", properties={"name": f"t{i}"}),
                       embedder=None)
            time.sleep(0.01)
        deadline = time.time() + 5
        while (est._dirty or getattr(est, "_saving", False) or est._save_timer is not None) and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(state["max"], 1)
        self.assertLess(state["n"], 10)          # coalesced, not one per write

    def test_a_write_during_the_save_is_still_saved(self):
        est, state = self._store()
        est.upsert(QuantumEntity(eid="membrane::t:a", cls="Component", properties={"name": "a"}), embedder=None)
        time.sleep(0.05)                         # the save is running now
        est.upsert(QuantumEntity(eid="membrane::t:b", cls="Component", properties={"name": "b"}), embedder=None)
        deadline = time.time() + 5
        while (est._dirty or getattr(est, "_saving", False) or est._save_timer is not None) and time.time() < deadline:
            time.sleep(0.02)
        again = EntityStore(path=est.path, shared=False)
        self.assertIn("membrane::t:b", again.docs)


if __name__ == "__main__":
    unittest.main()
