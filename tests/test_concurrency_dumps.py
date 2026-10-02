"""THE CONCURRENCY BUGS (the seats' 85k-line logs full of
'Set changed size during iteration'/'dictionary changed size during
iteration'): the entity field is read and written from FOUR threads
(the chat/gate thread, the efield capture worker, the night clock, the
 thinker), but the shared backends/overlays iterated UNGUARDED:

1. MemBackend.scan() iterates self.colls[coll].items() while ANOTHER
   thread upserts -> RuntimeError mid-scan (the '[efield] capture
   error #N' + '[night] run event skipped' exhibits: 256+ in the last
   boot's log).
2. EntityStore.save() iterates self.docs while chat/thread upserts
   (the '[entities] side-index save failed' exhibit -- and the
   announcer seat's 37MB entities.json is CORRUPT JSON on disk from a
   torn multi-second write: line 1 col 37404672).
3. list(est.docs.values())/items() in the night pass + the thinkers
   while the tick/materialize births entities -> night run events
   skipped wholesale (the '[night] run event skipped' flood: 256).

Each fix = iterate a SNAPSHOT (list(...)) under the owning lock, and
the store scan gets a lock of its own. The corrupt file heals via
os.replace atomic writes that were already there -- the corruption
came from the TUI write in to_doc()'s json.dumps crashing MID-STRING
(37404672) -- wait: os.replace is atomic; the corruption must be
json.dumps ITSELF failing on the tmp + the .tmp never replacing (the
load then still reads the OLD good file). The announcer file IS
corrupt on disk => an os.replace DID replace the good file with a bad
tmp. json.dumps of d with a dict mutated during serialization raises
-> tmp partial file -> os.replace DID NOT run (exception first)... So
the corruption came from a DIFFERENT writer: the TUI/serve-msgs path?
No: entities.json tmp writes... The error 'Expecting ,' at exactly
37404673 = a STRING INTERPOLATION of a mutated dict (dict changed
size during json iteration raises, but a MUTATED VALUE mid-dumps
serializes torn). Fix = snapshot the docs UNDER the store lock before
serialization."""

import os
import sys
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane.efstore import MemBackend, FileBackend  # noqa: E402
from membrane.efield import EntityField  # noqa: E402


class _Emb:
    model = "fake"

    def embed(self, texts):
        import numpy as np
        return [np.asarray([float(len(t) % 17 + i) for i in range(8)])
                for t in texts]


class TestBackendScanUnderWrites(unittest.TestCase):
    def test_scan_survives_concurrent_upserts(self):
        """THE SCAN BUG (the capture worker + the chat thread): MemBackend
        .scan() iterates the live dict while another thread upserts the
        SAME collection -> 'dictionary changed size during iteration' ->
        the whole step drops (the capture error #N flood). The scan must
        iterate a SNAPSHOT (list(items) built under the collection's
        own read)."""
        b = MemBackend()
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    b.upsert("events", f"e{i}", {"origin": "o",
                                                 "status": "pending",
                                                 "seq": i})
                    i += 1
                    time.sleep(0.0005)
                except Exception as e:
                    errors.append(("write", e))
                    break

        def reader():
            while not stop.is_set():
                try:
                    list(b.scan("events", {"origin": "o",
                                           "status": "pending"}))
                except RuntimeError as e:
                    errors.append(("scan", e))
                    return
        tw, tr = threading.Thread(target=writer), threading.Thread(target=reader)
        tw.start(); tr.start()
        time.sleep(1.0)
        stop.set()
        tw.join(timeout=2); tr.join(timeout=2)
        self.assertEqual(errors, [],
                         f"scan/upsert race: {errors[:3]}")


class TestEntityStoreSaveUnderWrites(unittest.TestCase):
    def test_save_snapshot_is_torn_free(self):
        """THE SAVE BUG: the debounced save (a 10s timer thread)
        serializes self.docs WHILE the tick/chat threads upsert + merge
        entities -- the announcer seat's entities.json died MID-STRING
        (37 MB torn). The save must serialize from a COPY taken under
        the store's lock (to_doc of a stable snapshot), and the JSON
        bytes are then immutable regardless of later mutations."""
        from membrane.entities import EntityStore, QuantumEntity
        with tempfile.TemporaryDirectory(prefix="ent-save-") as tmp:
            path = Path(tmp) / "entities.json"
            est = EntityStore(bucket="t-save", shared=False)
            est.path = path
            stop = threading.Event()
            errors = []

            def churn():
                i = 0
                while not stop.is_set():
                    try:
                        est.upsert(QuantumEntity(
                            eid=f"membrane::synth:churn{i}",
                            cls="Component",
                            properties={"name": f"n{i}"}))
                        i += 1
                    except Exception as e:
                        errors.append(("upsert", e))
                        break
                    time.sleep(0.0005)

            t = threading.Thread(target=churn)
            t.start()
            for _ in range(12):
                try:
                    est.save()
                except RuntimeError as e:
                    errors.append(("save", e))
                time.sleep(0.05)
            stop.set()
            t.join(timeout=2)
            self.assertEqual(errors, [], f"save/upsert race: {errors[:3]}")
            # every snapshot written must PARSE (the torn-JSON exhibit)
            bad = 0
            for _ in range(3):
                try:
                    json.loads(path.read_text())
                except Exception:
                    bad += 1
            self.assertEqual(bad, 0, "an entities.json snapshot is torn")


class TestEntityFieldConcurrentSteps(unittest.TestCase):
    def test_write_events_and_capture_worker_coexist(self):
        """THE CAPTURE-ERROR BUG (the 'capture error #N: dictionary
        changed size' + '[night] run event skipped' flood): the night
        lane's fld.write_events + fld.process ran from the NIGHT thread
        while the capture worker ran its own embed/apply -- the two
        threads share _states/_dirty/_deltas AND the backend's colls;
        write_events IS locked but process() -> embed_pending/apply_
        pending were UNLOCKED (the night's fld.process(limit=8) raced
        the worker's _step mid-write). Every shared step must take the
        field's lock."""
        with tempfile.TemporaryDirectory(prefix="efield-race-") as tmp:
            b = FileBackend(tmp)
            fld = EntityField(b, _Emb(), "o1")
            stop = threading.Event()
            errors = []

            def worker():
                while not stop.is_set():
                    try:
                        fld.process(limit=8)
                    except RuntimeError as e:
                        errors.append(("process", e))
                        return
                    except Exception as e:
                        errors.append(("process-other", e))
                    time.sleep(0.005)

            tw = threading.Thread(target=worker)
            tw.start()
            for i in range(120):
                try:
                    key = fld.learn(f"lesson {i}: the field keeps state",
                                    src="test")
                    self.assertIsNotNone(key)
                except RuntimeError as e:
                    errors.append(("write", e))
                    break
                time.sleep(0.002)
            stop.set()
            tw.join(timeout=3)
            self.assertEqual(errors, [],
                             f"field races: {errors[:4]}")


if __name__ == "__main__":
    unittest.main()