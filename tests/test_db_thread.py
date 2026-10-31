"""One handle, one database thread, a priority queue (operator, 2026-10-31: "why cant you
keep shared handle? why cant you queue db operations on that handle?"). A turn's
operation runs before queued background work; nothing holds the handle across a batch."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite as CB  # noqa: E402

try:
    from membrane.cblstore import CBLiteBackend
    HAVE = CB.available() if hasattr(CB, "available") else True
    CBLiteBackend(Path(tempfile.mkdtemp()) / "probe.cblite").close()
except Exception:
    HAVE = False


@unittest.skipUnless(HAVE, "Couchbase Lite libraries not fetched (scripts/fetch-cbl.sh)")
class TestTheDatabaseThread(unittest.TestCase):
    def setUp(self):
        self.b = CBLiteBackend(Path(tempfile.mkdtemp()) / "f.cblite")
        self.db = self.b.db

    def tearDown(self):
        self.b.close()

    def test_every_operation_runs_on_one_thread(self):
        seen = set()
        ts = [threading.Thread(target=lambda: seen.add(self.db._run(threading.get_ident)))
              for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(5)
        self.assertEqual(len(seen), 1)
        self.assertNotIn(threading.get_ident(), seen)

    def test_a_turns_operation_goes_before_queued_background_work(self):
        busy, release, order = threading.Event(), threading.Event(), []

        def bg(tag, block=False):
            with CB.background():
                def op():
                    if block:
                        busy.set()
                        release.wait(10)
                    order.append(tag)
                self.db._run(op)
        blocker = threading.Thread(target=bg, args=("block", True))
        blocker.start()
        self.assertTrue(busy.wait(5))
        queued = [threading.Thread(target=bg, args=(f"bg{i}",)) for i in range(3)]
        for t in queued:
            t.start()
        while self.db._ops.qsize() < 3:
            pass
        turn = threading.Thread(target=lambda: self.db._run(lambda: order.append("turn")))
        turn.start()
        while self.db._ops.qsize() < 4:
            pass
        release.set()
        for t in [blocker, turn] + queued:
            t.join(5)
        self.assertEqual(order[0], "block")
        self.assertEqual(order[1], "turn", order)

    def test_a_turns_read_is_served_while_a_transaction_is_open(self):
        self.b.upsert("sentences", "s0", {"text": "t0"})
        self.b._cache.clear()
        with CB.background():
            self.db.begin()                     # the write-behind mid-transaction
        out = {}
        r = threading.Thread(target=lambda: out.update(v=self.b.get("sentences", "s0")))
        r.start()
        r.join(5)
        alive = r.is_alive()
        with CB.background():
            self.db.end()
        self.assertFalse(alive, "a turn's read waited on an open transaction")
        self.assertEqual(out["v"]["text"], "t0")


class TestOneOperationPerMultiRead(TestTheDatabaseThread):
    def test_a_cold_multi_read_is_one_queued_operation(self):
        """Profiled 08:33: the banner's get_multi queued one operation per key -- hundreds
        of thread hand-offs a banner -- while the database thread sat mostly idle."""
        for i in range(50):
            self.b.upsert("sentences", f"s{i}", {"text": f"t{i}"})
        self.b._cache.clear()
        n = {"ops": 0}
        real = self.db._ops.put

        def put(item, *a, **k):
            n["ops"] += 1
            return real(item, *a, **k)
        self.db._ops.put = put
        got = self.b.get_multi("sentences", [f"s{i}" for i in range(50)] + ["nope"])
        self.assertEqual(len(got), 50)
        self.assertEqual(n["ops"], 1)


if __name__ == "__main__":
    unittest.main()
