"""A WRITE NEVER RIDES IN ANOTHER CALLER'S TRANSACTION (2026-11-02, the operator: "chat got lost
again"). The database runs every operation on its one thread with a re-entrant lock, so a chat
row saved while the write-behind had a batch open became part of that batch: committed only at
the batch's end, and gone when the seat was cut before it. A write from any other caller now
commits on its own: the open batch is committed first, the write runs, the batch reopens.
Nothing here rolls a batch back on purpose; a cut process does, and that is what the test plays.
"""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402


@unittest.skipUnless(cblite.lib_paths() is not None, "CBL libraries not vendored")
class ForeignWriteCommits(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = cblite.Database(cblite.load(), "t", self.dir)
        for c in ("c", "messages"):        # the collections exist before any batch (a rollback drops a new one)
            self.db.put_json(c, "seed", "{}")

    def tearDown(self):
        self.db.close()

    def _batch_with_foreign_write(self, foreign):
        began, wrote = threading.Event(), threading.Event()

        def batch():                       # the write-behind: begin, write, then the seat is cut
            self.db.begin()
            self.db.put_json("c", "batch-1", '{"n":1}')
            began.set()
            wrote.wait(10)
            self.db.put_json("c", "batch-2", '{"n":2}')
            self.db.end(commit=False)      # the cut: the open batch never commits

        t = threading.Thread(target=batch)
        t.start()
        began.wait(10)
        foreign()
        wrote.set()
        t.join(10)

    def test_a_chat_row_saved_during_a_batch_survives_the_batch_being_cut(self):
        self._batch_with_foreign_write(lambda: self.db.put_json("messages", "alice-reply", '{"t":"hi"}'))
        self.assertEqual(self.db.get_json("messages", "alice-reply"), b'{"t":"hi"}')

    def test_the_batch_keeps_its_own_later_writes_in_its_own_transaction(self):
        self._batch_with_foreign_write(lambda: self.db.put_json("messages", "alice-reply", '{"t":"hi"}'))
        self.assertIsNone(self.db.get_json("c", "batch-2"))      # after the foreign write: still the batch's

    def test_a_foreign_batch_commits_on_its_own_too(self):
        def foreign():
            self.db.begin()
            self.db.put_json("messages", "alice-a", '{"t":"a"}')
            self.db.put_json("messages", "alice-b", '{"t":"b"}')
            self.db.end()
        self._batch_with_foreign_write(foreign)
        self.assertEqual(self.db.get_json("messages", "alice-b"), b'{"t":"b"}')

    def test_the_chat_store_saves_without_waiting_and_survives_the_cut(self):
        import time
        from membrane.server import MessageStore
        ms = MessageStore(None, cbl=self.db)
        info = {"id": "m1", "sessionID": "ses_alice", "role": "assistant", "time": {"created": 1}}
        took = {}

        def foreign():
            t0 = time.monotonic()
            ms.append("ses_alice", info, [{"type": "text", "text": "the reply"}])
            took["s"] = time.monotonic() - t0
        self._batch_with_foreign_write(foreign)
        self.assertLess(took["s"], 2.0, "a chat save waited for the batch")
        rows = MessageStore(None, cbl=self.db).list("ses_alice")
        self.assertEqual([p["text"] for r in rows for p in r["parts"]], ["the reply"])

    def test_one_caller_alone_still_batches(self):
        self.db.begin()
        self.db.put_json("c", "x", '{"n":1}')
        self.db.end(commit=False)
        self.assertIsNone(self.db.get_json("c", "x"))



@unittest.skipUnless(cblite.lib_paths() is not None, "CBL libraries not vendored")
class AGoalSurvivesTheCut(unittest.TestCase):
    """The entity store (goals live there) saves in a batch of its own: before, that batch nested
    inside the write-behind's open one and a cut rolled the goal back (goal 38b98d393565,
    2026-11-02). Now it waits for the open batch to end and commits by itself."""

    def test_a_goal_saved_during_the_write_behinds_batch_survives_the_cut(self):
        import os
        os.environ.setdefault("MEMBRANE_STORE", "json")
        from membrane.cblstore import CBLiteBackend
        from membrane.entities import EntityStore, QuantumEntity
        d = Path(tempfile.mkdtemp())
        be = CBLiteBackend(d / "field.cblite")
        try:
            db = be.db
            db.put_json("c", "seed", "{}")
            est = EntityStore(path=str(d / "entities.json"), shared=False, cbl=db)
            est.save()                                     # its collections exist before the batch
            began, release = threading.Event(), threading.Event()

            def batch():
                db.begin()
                db.put_json("c", "batch-1", '{"n":1}')
                began.set()
                release.wait(10)
                db.end(commit=False)                       # the cut
            t = threading.Thread(target=batch)
            t.start()
            began.wait(10)
            est.upsert(QuantumEntity(eid="membrane::goal:alice1", cls="Goal",
                                     properties={"name": "ship it", "status": "open"}), embedder=None)
            saver = threading.Thread(target=est.save)
            saver.start()
            saver.join(1.0)                                # the save gets its moment while the batch is open
            release.set()
            t.join(10)
            saver.join(10)
            again = EntityStore(path=str(d / "entities.json"), shared=False, cbl=db)
            self.assertIn("membrane::goal:alice1", again.docs)
        finally:
            be.close()


if __name__ == "__main__":
    unittest.main()
