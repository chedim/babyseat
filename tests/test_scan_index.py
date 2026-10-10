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


class TestRangeIndexAndIncrementalChanges(unittest.TestCase):
    """The seq range index + changes_since's incremental read: always the
    rows a fresh full read gives, in the same order."""

    def _field(self):
        from membrane.efield import EntityField
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_cliffhanger import FakeEmbedder  # noqa: E402
        return EntityField(ES.MemBackend(), FakeEmbedder(), "seat@t")

    def _fresh(self, f, seq):
        f.__dict__.pop("_cs_memo", None)
        return f.changes_since(seq)

    def test_range_scans_match_the_full_walk(self):
        rnd = random.Random(3)
        b = ES.MemBackend()
        for step in range(2500):
            k = f"d{rnd.randrange(300)}"
            op = rnd.random()
            if op < 0.6:
                b.upsert("entity_deltas", k, {"origin": "o", "seq": rnd.randrange(200), "sidx": rnd.randrange(3), "eid": f"x{rnd.randrange(9)}"})
            elif op < 0.75:
                b.delete("entity_deltas", k)
            else:
                b.insert_if_absent("entity_deltas", k, {"origin": "o", "seq": rnd.randrange(200), "sidx": 0, "eid": "y"})
            if step % 101 == 0:
                for lo in (0, 50, 150, 199):
                    for want in ({"gt": lo}, {"gte": lo}):
                        eq = {"origin": "o", "seq": want}
                        self.assertEqual(list(b.scan("entity_deltas", eq, order=("seq", "sidx"))),
                                         full_scan(b, "entity_deltas", eq, ("seq", "sidx")), (lo, want))
                        self.assertEqual(sorted(k for k, _ in b.scan("entity_deltas", eq)),
                                         sorted(k for k, _ in full_scan(b, "entity_deltas", eq)))

    def test_incremental_changes_since_equals_a_fresh_read(self):
        rnd = random.Random(11)
        f = self._field()
        f.flush = lambda: None                                   # no capture queue in this test
        seq = 0
        for step in range(1500):
            op = rnd.random()
            if op < 0.8:
                seq += rnd.choice([0, 1, 1, 2])                  # several rows per event, sometimes
                f.b.upsert("entity_deltas", f"d{step}", {"origin": f.origin, "seq": seq, "sidx": rnd.randrange(3),
                                                        "eid": rnd.choice(["f:a", "f:b", "e:x", "person:p", "f:c"]),
                                                        "event": f"ev{seq}", "stream": "discourse"})
            elif op < 0.9:
                f.b.delete("entity_deltas", f"d{rnd.randrange(step + 1)}")
            else:                                                # an older seq arrives late
                f.b.upsert("entity_deltas", f"late{step}", {"origin": f.origin, "seq": max(0, seq - 5), "sidx": 0,
                                                           "eid": "f:a", "event": "late", "stream": "code"})
            if step % 37 == 0:
                for wm in (0, 7):
                    got = f.changes_since(wm)
                    memo = f.__dict__.get("_cs_memo")
                    ref = self._fresh(f, wm)
                    f.__dict__["_cs_memo"] = memo                 # keep the incremental state going
                    self.assertEqual(got, ref, (step, wm))


class TestDeltaIndexKeeps(unittest.TestCase):
    """deltas_of() after many flushes and deletes equals a fresh rebuild --
    the index merges a flush's own deltas instead of being thrown away."""

    def test_merged_index_equals_a_rebuild(self):
        from membrane.efield import EntityField
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_cliffhanger import FakeEmbedder  # noqa: E402
        rnd = random.Random(5)
        f = EntityField(ES.MemBackend(), FakeEmbedder(), "seat@t")
        eids = ["f:a", "f:b", "f:c", "e:x"]
        seq, built = 0, 0
        f.deltas_of("f:a")                                   # the index exists from the start
        for step in range(1200):
            r = rnd.random()
            if r < 0.7:
                for _ in range(rnd.randrange(1, 4)):
                    seq += rnd.choice([0, 1])
                    e = rnd.choice(eids)
                    sq = seq if rnd.random() > 0.1 else max(0, seq - rnd.randrange(1, 6))   # sometimes late
                    sx = rnd.randrange(3)
                    f._deltas[f"d:{f.o}:{e}:{sq}:{sx}"] = {"_k": "dl", "eid": e, "origin": f.origin,
                                                          "seq": sq, "sidx": sx, "v": step}
                f.flush()
            elif r < 0.8:
                keys = [k for k, _ in f.b.scan("entity_deltas")]
                if keys:
                    f.b.delete("entity_deltas", rnd.choice(keys))
            if step % 41 == 0:
                got = {e: f.deltas_of(e) for e in eids}
                kept_gen = f._deltas_index_gen
                f.rebuild_delta_index()
                ref = {e: f.deltas_of(e) for e in eids}
                self.assertEqual(got, ref, step)
                built += 1
        self.assertGreater(built, 20)

    def test_a_flush_without_new_deltas_keeps_the_index(self):
        from membrane.efield import EntityField
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_cliffhanger import FakeEmbedder  # noqa: E402
        f = EntityField(ES.MemBackend(), FakeEmbedder(), "seat@t")
        f._deltas["d1"] = {"eid": "f:a", "origin": f.origin, "seq": 1, "sidx": 0}
        f.flush()
        f.deltas_of("f:a")
        idx = f._deltas_index
        f.flush(); f.flush()
        self.assertIs(f._deltas_index, idx)                  # not thrown away


class TestSeedTopicsIncremental(unittest.TestCase):
    """The field-grown intent seeds: the incremental look between full scans
    finds exactly what a full scan finds."""

    def test_incremental_equals_full(self):
        from membrane import lanes as L
        from membrane.efield import EntityField
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_cliffhanger import FakeEmbedder  # noqa: E402
        f = EntityField(ES.MemBackend(), FakeEmbedder(), "seat@t")
        srv = type("S", (), {})(); srv.efield = f; srv.field_seeds = True
        L._SEED_CACHE["field"] = {"size": -1, "matrix": None, "thr": None, "topics": [], "scanned": False}
        rnd = random.Random(2)
        texts = ["remember that the boat is blue", "what's the battery", "forget the old note",
                 "recall the anchor depth", "hello there", "note that we leave at nine"]
        n = 0
        for step in range(60):
            n += 1
            f.b.upsert("events", f"ev{n}", {"origin": f.origin, "stream": "discourse", "role": rnd.choice(["user", "model", "reasoning"]),
                                           "text": rnd.choice(texts), "n": n})
            f._ostats["ev_n"] = n
            got = list(L._field_seed_topics(srv))
            keep = dict(L._SEED_CACHE["field"])
            L._SEED_CACHE["field"] = {"size": -1, "matrix": None, "thr": None, "topics": [], "scanned": False}
            ref = list(L._field_seed_topics(srv))
            L._SEED_CACHE["field"] = keep
            self.assertEqual(got, ref, step)
        self.assertTrue(any(fam == "learn" for _, fam, _ in got))
