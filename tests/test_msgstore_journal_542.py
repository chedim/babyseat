"""#542: an append costs an append. MessageStore journals the rows it
changed beside serve-msgs.json; readers (server load, stats.load_store)
replay the journal over the snapshot; compaction folds it back atomically."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import stats
from membrane.server import MessageStore


def _row(mid, created, text="x", completed=None):
    tm = {"created": created}
    if completed:
        tm["completed"] = completed
    return ({"id": mid, "sessionID": "s1", "role": "user", "time": tm},
            [{"id": mid + "-p", "type": "text", "text": text}])


class TestJournal(unittest.TestCase):
    def test_append_does_not_rewrite_the_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            big = {"s0": [{"info": {"id": f"m{i}", "role": "user",
                                    "time": {"created": i}}, "parts": []}
                          for i in range(2000)]}
            Path(p).write_text(json.dumps(big))
            before = Path(p).read_bytes()
            ino = os.stat(p).st_ino
            s = MessageStore(p)
            s.append("s1", *_row("a", 5))
            self.assertEqual(Path(p).read_bytes(), before)
            self.assertEqual(os.stat(p).st_ino, ino)
            self.assertTrue(os.path.getsize(p + ".jnl") < len(before) // 10)

    def test_reload_replays_journal_with_upsert_and_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            s = MessageStore(p)
            s.append("s1", *_row("a", 1, "first"))
            s.append("s1", *_row("b", 2, "second"))
            s.append("s1", *_row("a", 1, "first-final", completed=9))
            s.flush()
            for rows in (MessageStore(p).list("s1"),
                         stats.load_store(p)["s1"]):
                self.assertEqual([r["info"]["id"] for r in rows], ["a", "b"])
                self.assertEqual(rows[0]["parts"][0]["text"], "first-final")

    def test_torn_last_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            s = MessageStore(p)
            s.append("s1", *_row("a", 1))
            with open(p + ".jnl", "a") as f:
                f.write('{"sid": "s1", "row": {"info": {"id": "b"')
            self.assertEqual([r["info"]["id"] for r in
                              MessageStore(p).list("s1")], ["a"])
            self.assertEqual(len(stats.load_store(p)["s1"]), 1)

    def test_compaction_folds_journal_into_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            s = MessageStore(p)
            for i in range(40):
                s.append("s1", *_row(f"m{i}", i, "y" * 50))
            snap = json.loads(Path(p).read_text())
            self.assertGreater(len(snap.get("s1", [])), 0)
            jn = os.path.getsize(p + ".jnl") if os.path.exists(p + ".jnl") else 0
            self.assertLessEqual(jn, os.path.getsize(p))
            self.assertEqual(len(MessageStore(p).list("s1")), 40)

    def test_compaction_between_the_two_reads_loses_nothing(self):
        # review of #660: a reader that read the old snapshot and then the
        # emptied journal saw neither the folded rows nor the journal's.
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            s = MessageStore(p)
            s.append("s1", *_row("a", 1, "z" * 4000))
            with s._file_lock():
                s._compact_locked()          # a is in the snapshot (big)
            s.append("s1", *_row("b", 2))    # b is only in the journal
            real, calls = Path.read_text, []

            def hooked(self_, *a, **k):
                out = real(self_, *a, **k)
                if not calls:                # after the FIRST file is read
                    calls.append(1)
                    with s._file_lock():
                        s._compact_locked()  # fold + empty the journal
                return out
            Path.read_text = hooked
            try:
                got = stats.read_raw(p)
            finally:
                Path.read_text = real
            self.assertEqual(sorted(r["info"]["id"] for r in got["s1"]),
                             ["a", "b"])

    def test_flush_with_nothing_dirty_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            s = MessageStore(p)
            s.append("s1", *_row("a", 1))
            before = os.path.getsize(p + ".jnl") if os.path.exists(p + ".jnl") else 0
            snap = os.path.getmtime(p) if os.path.exists(p) else 0
            s.flush()
            after = os.path.getsize(p + ".jnl") if os.path.exists(p + ".jnl") else 0
            self.assertEqual(before, after)
            self.assertEqual(snap, os.path.getmtime(p) if os.path.exists(p) else 0)

    def test_delete_survives_journal(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "serve-msgs.json")
            s = MessageStore(p)
            s.append("s1", *_row("a", 1))
            s.append("s2", *_row("b", 2))
            s.delete("s1")
            again = MessageStore(p)
            self.assertEqual(again.list("s1"), [])
            self.assertEqual(len(again.list("s2")), 1)


if __name__ == "__main__":
    unittest.main()
