"""ONE FETCH PER READ: Database.get_json asked the native lib for the
document twice (two GetDocument calls, each under its own lock) and released
only the second handle -- every cold read fetched twice and leaked one
native document. A read fetches once and releases every handle it got,
for a present document and for a missing one. The fake-lib tests run
everywhere; the real-lib test runs where the CBL libraries are vendored."""
import ctypes as C
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import cblite  # noqa: E402


class FakeL:
    """Just the calls get_json/exists make; hands out fresh handles."""

    def __init__(self, docs):
        self.docs = docs
        self.got, self.released = [], []
        self._next = 1000

    def CBLCollection_GetDocument(self, coll, key, err):
        k = C.string_at(key.buf, key.size).decode()
        if k not in self.docs:
            return None
        self._next += 1
        self.got.append((self._next, k))
        return self._next

    def CBLDocument_CreateJSON(self, d):
        return ("json", d)

    def CBL_Release(self, h):
        self.released.append(h)


class FakeLib:
    def __init__(self, L):
        self.L = L

    def take(self, r):
        _, d = r
        k = dict(self.L.got)[d]
        return self.L.docs[k].encode()

    def check(self, ok, err, what):
        return ok


def fake_db(docs):
    db = cblite.Database.__new__(cblite.Database)
    db.lib = FakeLib(FakeL(docs))
    db.lock = threading.RLock()
    db.turns = cblite.TurnFirst()
    db.scope = "membrane"
    db.colls = {"c": 1}
    db._q = {}
    return db


class TestGetJsonFetchesOnce(unittest.TestCase):
    def test_present_doc_fetched_once_and_released(self):
        db = fake_db({"k": '{"a":1}'})
        self.assertEqual(db.get_json("c", "k"), b'{"a":1}')
        L = db.lib.L
        self.assertEqual(len(L.got), 1, "get_json fetched the document more than once")
        self.assertEqual(sorted(h for h, _ in L.got), sorted(L.released),
                         "a fetched document handle was never released")

    def test_missing_doc_fetched_once(self):
        db = fake_db({})
        self.assertIsNone(db.get_json("c", "nope"))
        self.assertEqual(db.lib.L.released, [])

    def test_empty_key_never_reaches_the_lib(self):           # #11 / #28
        db = fake_db({"": "{}"})
        self.assertIsNone(db.get_json("c", ""))
        self.assertIsNone(db.get_json("c", None))
        self.assertEqual(db.lib.L.got, [])

    def test_many_reads_balance(self):
        db = fake_db({"a": "{}", "b": "[]"})
        for k in ("a", "b", "a", "x", "b"):
            db.get_json("c", k)
        L = db.lib.L
        self.assertEqual(len(L.got), 4)
        self.assertEqual(sorted(h for h, _ in L.got), sorted(L.released))


@unittest.skipUnless(cblite.lib_paths() is not None, "CBL libraries not vendored")
class TestGetJsonRealLib(unittest.TestCase):
    """The real library, its two calls counted through a pass-through."""

    def test_real_get_json_fetches_once_and_releases(self):
        lib = cblite.load()
        real = lib.L
        counts = {"get": [], "rel": []}

        class Counting:
            def __getattr__(self, name):
                return getattr(real, name)

            def CBLCollection_GetDocument(self, *a):
                h = real.CBLCollection_GetDocument(*a)
                if h:
                    counts["get"].append(h)
                return h

            def CBL_Release(self, h):
                counts["rel"].append(h)
                return real.CBL_Release(h)

        class Shim:
            L = Counting()
            take = lib.take
            check = lib.check

        with tempfile.TemporaryDirectory() as d:
            db = cblite.Database(lib, "t", d)
            try:
                db.put_json("c", "k", '{"a":1}')
                db.lib = Shim()
                counts["get"].clear(), counts["rel"].clear()
                self.assertEqual(db.get_json("c", "k"), b'{"a":1}')
                self.assertIsNone(db.get_json("c", "missing"))
                self.assertEqual(len(counts["get"]), 1)
                self.assertEqual(counts["get"], counts["rel"])
            finally:
                db.lib = lib
                db.close()


if __name__ == "__main__":
    unittest.main()
