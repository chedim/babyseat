"""THE SCAN INDEX (2026-10-10): scan() by session through the index gives
exactly the full scan's rows, in the same order, through every kind of write."""
import os
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import efstore as ES  # noqa: E402


def full_scan(b, coll, eq, order=()):
    """The reference: the plain walk over every row."""
    with b._ram_lock:
        items = [(k, dict(d)) for k, d in b._c(coll).items() if ES.matches(d, eq)]
    for f in reversed(order):
        items.sort(key=lambda kd, f=f: 0 if kd[1].get(f) is None else kd[1].get(f))
    return items


class TestScanIndex(unittest.TestCase):
    def test_same_rows_same_order_through_every_write(self):
        rnd = random.Random(7)
        b = ES.MemBackend()
        sessions = [f"s{i}" for i in range(6)] + [None]
        list(b.scan("events", {"session": "s1"}))          # build the index early: the writes must keep it
        for step in range(3000):
            k = f"e{rnd.randrange(400)}"
            op = rnd.random()
            doc = {"session": rnd.choice(sessions), "seq": rnd.randrange(1000), "role": rnd.choice(["user", "model"])}
            if op < 0.45:
                b.upsert("events", k, doc)
            elif op < 0.6:
                b.insert_if_absent("events", k, doc)
            elif op < 0.7:
                b.upsert_multi("events", {k: doc, f"e{rnd.randrange(400)}": dict(doc, seq=1)})
            elif op < 0.8:
                b.delete("events", k)
            elif op < 0.9:
                b.cas_update("events", k, lambda d, doc=doc: None if rnd.random() < 0.3 else doc)
            else:
                b.cas_update("events", k, lambda d: dict(d, seq=(d or {}).get("seq", 0) + 1) if d else None)
            if step % 97 == 0:
                for s in sessions:
                    for eq, order in (({"session": s}, ()), ({"session": s, "role": "model", "seq": {"gt": 300}}, ("seq",))):
                        got = list(b.scan("events", eq, order=order))
                        ref = full_scan(b, "events", eq, order)
                        self.assertEqual(got, ref, (s, eq))        # same rows, same ORDER

    def test_unindexed_shapes_still_scan_fully(self):
        b = ES.MemBackend()
        for i in range(20):
            b.upsert("events", f"e{i}", {"session": f"s{i % 3}", "seq": i})
        self.assertEqual(len(list(b.scan("events", {"session": ["s0", "s1"]}))), 14)
        self.assertEqual(len(list(b.scan("events", {"seq": {"lt": 5}}))), 5)
        self.assertEqual(len(list(b.scan("events"))), 20)

    def test_a_session_that_never_changes_keeps_insertion_order(self):
        b = ES.MemBackend()
        for i in range(50):
            b.upsert("events", f"e{i}", {"session": "s", "seq": i})
        list(b.scan("events", {"session": "s"}))
        for i in range(50, 60):
            b.insert_if_absent("events", f"e{i}", {"session": "s", "seq": i})
        b.upsert("events", "e3", {"session": "s", "seq": 3, "x": 1})       # an update keeps its place
        self.assertEqual([k for k, _ in b.scan("events", {"session": "s"})], [f"e{i}" for i in range(60)])

    def test_boot_loaded_rows_are_indexed(self):
        b = ES.MemBackend()
        b.colls["events"]["a"] = {"session": "s"}           # the FileBackend's boot load writes directly
        self.assertEqual([k for k, _ in b.scan("events", {"session": "s"})], ["a"])


if __name__ == "__main__":
    unittest.main()
