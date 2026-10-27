"""A RUN NEVER WAITS ON THE EMBEDDER (#617, 2026-10-27: Memo's code_run and
method_call timed out at 120 s; a 50 ms grep held its call for 7-120 s).

run_command_entity wrote its observation event and then embedded the field's
whole pending backlog on the caller's thread, through an Ollama busy with the
thinker's and the chat's model calls. Pinned: with a capture worker running,
the run returns while the embedder is still blocked, and its event lands on
the worker; without one (tests, capture off) the event is written inline.
"""
import os
import sys
import threading
import unittest
from pathlib import Path

os.environ.setdefault("MEMBRANE_STORE", "json")
os.environ.setdefault("MEMBRANE_RUN", "0")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane.capture import Capturer  # noqa: E402
from membrane.efield import EntityField  # noqa: E402
from membrane.efstore import MemBackend  # noqa: E402
from membrane.entities import EntityStore  # noqa: E402
from membrane.night import run_command_entity  # noqa: E402


class BusyEmbedder(FakeEmbedder):
    """An embedder stuck behind someone else's model call until released."""
    def __init__(self):
        super().__init__()
        self.free = threading.Event()

    def embed(self, texts):
        self.free.wait(30)
        return super().embed(texts)


def _observations(f):
    return [d for _, d in f.b.scan("events", {"origin": f.origin})
            if d.get("stream") == "observation" and d.get("tool") == "shell"]


class TestARunNeverWaits(unittest.TestCase):
    def test_a_run_returns_while_the_embedder_is_busy(self):
        emb = BusyEmbedder()
        f = EntityField(MemBackend(), emb, "seat@host")
        cap = Capturer(f, mode="on")
        est = EntityStore(path=None, shared=False)
        done = {}
        t = threading.Thread(target=lambda: done.update(run_command_entity(f, est, None, "echo hi")))
        t.start()
        t.join(5)
        try:
            self.assertFalse(t.is_alive(), "the run waited on the embedder")
            self.assertEqual(done.get("rc"), 0)
        finally:
            emb.free.set()
            t.join(10)
        self.assertTrue(cap.flush(10))
        self.assertEqual(len(_observations(f)), 1)                 # the worker wrote it

    def test_without_a_worker_the_event_is_written_inline(self):
        f = EntityField(MemBackend(), FakeEmbedder(), "seat@host")
        run_command_entity(f, EntityStore(path=None, shared=False), None, "echo hi")
        self.assertEqual(len(_observations(f)), 1)


if __name__ == "__main__":
    unittest.main()
