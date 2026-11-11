"""THE WORD INDEX FROM THE WARM SNAPSHOT (#1115): the boot rebuilt the field's word index from every
event (450k sentences, 20-36 s on a background thread that held the GIL against the rest of the boot).
The clean stop keeps it beside the hot rows; the next boot installs it only when the rows came from
the same snapshot AND it covers exactly what a rebuild would (the same events and chunks, the same
number of sentences per event). Anything else rebuilds as before.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _ef_fakes import FakeEmbedder  # noqa: E402
from membrane import efield as EF  # noqa: E402
from membrane import lexical as LX  # noqa: E402
from membrane.sqlstore import SQLiteBackend  # noqa: E402

TEXTS = ["The license is PolyForm Noncommercial.", "MEMBRANE_JWT_SECRET guards the seat door.",
         "Issue #277 named the self Blurp.", "The reranker is bge-reranker-v2-m3 on mps.",
         "seat-token lives next to the field."]


def _field(path):
    return EF.EntityField(SQLiteBackend(path), FakeEmbedder(), "seat@host")


def _ask(ix):
    return [ix.search(q) for q in ("PolyForm", "MEMBRANE_JWT_SECRET", "#277", "bge-reranker-v2-m3", "seat-token")]


class _Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lex-snap-")
        self.path = Path(self.dir) / "field.sqlite"
        f = _field(self.path)
        for t in TEXTS:
            f.learn(t)
        f.process()
        self.ix = LX.index_of(f)
        self.want = _ask(self.ix)
        self.assertTrue(any(self.want))
        self.f = f

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _stop(self, blob=None):
        self.f.b.close()
        return self.f.b.save_warm(lex=LX.dump(self.f) if blob is None else blob)

    def _boot(self, build_ok=True):
        f2 = _field(self.path)
        guard = (mock.patch.object(LX, "build", wraps=LX.build) if build_ok else
                 mock.patch.object(LX, "build", side_effect=AssertionError("rebuilt")))
        with guard as b:
            th = LX.warm(f2)
            if th is not None:
                th.join(30)
        return f2, b


class TheRoundTrip(_Case):
    def test_the_index_comes_back_and_answers_the_same(self):
        man = self._stop()
        self.assertTrue(man.get("lex"), man)
        f2, b = self._boot()
        try:
            b.assert_not_called()
            self.assertIsInstance(f2._lex, LX.LexIndex)
            self.assertEqual(_ask(LX.index_of(f2)), self.want)
            self.assertIsNone(getattr(f2.b, "_warm_lex", None))       # handed over once
        finally:
            f2.b.close()


class TheTeardownHandsItOver(_Case):
    def test_the_seats_save_passes_the_index_to_the_store(self):
        from membrane import restarts
        self.f.b.close()
        srv = type("S", (), {"efield": self.f})()
        restarts.save_warm(srv)
        man = __import__("json").loads((self.path / "warm" / "manifest.json").read_text())
        self.assertTrue(man.get("lex"), man)

    def test_a_store_without_the_index_slot_still_saves(self):
        from membrane import restarts
        be = mock.Mock(spec=["save_warm"])
        be.save_warm.return_value = {"mats": []}
        restarts.save_warm(type("S", (), {"efield": type("F", (), {"b": be, "_lex": None})()})())
        be.save_warm.assert_called_once_with()


class TheRebuilds(_Case):
    def test_an_index_that_misses_an_event_rebuilds(self):
        with self.ix.lock:
            self.ix.seen.discard(next(iter(self.ix.seen)))
        self._stop()
        f2, b = self._boot()
        try:
            b.assert_called_once()
            self.assertEqual(_ask(LX.index_of(f2)), self.want)
        finally:
            f2.b.close()

    def test_a_damaged_index_rebuilds(self):
        self._stop(blob=b"\x00not an index")
        f2, b = self._boot()
        try:
            b.assert_called_once()
            self.assertEqual(_ask(LX.index_of(f2)), self.want)
        finally:
            f2.b.close()

    def test_no_rows_snapshot_no_index_snapshot(self):
        self.f.b.close()
        with mock.patch.object(SQLiteBackend, "_save_rows", return_value=None):
            man = self.f.b.save_warm(lex=LX.dump(self.f))
        self.assertFalse(man.get("lex"))

    def test_an_index_still_building_is_not_dumped(self):
        self.f._lex = LX._BUILDING
        self.assertIsNone(LX.dump(self.f))


if __name__ == "__main__":
    unittest.main()
