"""#720: a planned restart keeps the warm vector matrices. The teardown
writes them to the field folder after the last flush; the next boot loads
them instead of rebuilding (a 40k x 1024 build GIL-starves the first gates);
a snapshot that no longer matches the store is dropped, never trusted."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from membrane import efstore  # noqa: E402
from membrane import restarts  # noqa: E402
from membrane.efstore import FileBackend, pack_f16  # noqa: E402


def _vec(i, d=8):
    v = np.zeros(d)
    v[i % d] = 1.0
    v[(i + 1) % d] = 0.5
    return v


class TestWarmSnapshot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        be = FileBackend(self.dir)
        for i in range(5):
            be.upsert("events", f"e{i}", {"text": f"t{i}", "vec": pack_f16(_vec(i))})
        be.upsert("chunks", "c1", {"vec": pack_f16(_vec(3))})
        self.assertIsNotNone(be._vec_matrix("events", "vec", None))
        self.assertIsNotNone(be._vec_matrix("chunks", "vec", None))
        be.close()
        self.be = be

    def _boot_without_building(self):
        with mock.patch.object(efstore, "_build_matrix",
                               side_effect=AssertionError("rebuilt at boot")):
            return FileBackend(self.dir)

    def test_a_snapshot_loads_at_boot_without_a_rebuild(self):
        self.be.save_warm()
        be2 = self._boot_without_building()
        self.assertEqual(sorted(be2.vec_status()["built"]),
                         ["chunks.vec", "events.vec"])
        top = be2.vector_top_k("events", "vec", _vec(2), 1)
        self.assertEqual(top[0][0], "e2")

    def test_a_write_after_the_snapshot_drops_it(self):
        self.be.save_warm()
        import json                                  # a write the snapshot never saw
        with (Path(self.dir) / "events.jsonl").open("a") as fh:
            fh.write(json.dumps({"k": "e9", "d": {"vec": pack_f16(_vec(6))}}) + "\n")
        be3 = FileBackend(self.dir)
        self.assertEqual(list(be3.vec_status()["built"]), ["chunks.vec"])
        self.assertFalse(any((Path(self.dir) / efstore.WARM_DIR).glob("*")))

    def test_another_format_version_is_dropped(self):
        self.be.save_warm()
        with mock.patch.object(efstore, "WARM_VERSION", efstore.WARM_VERSION + 1):
            be2 = FileBackend(self.dir)
        self.assertEqual(be2.vec_status()["built"], {})

    def test_a_dirty_matrix_is_rebuilt_before_it_is_saved(self):
        be = FileBackend(self.dir)
        be._vec_matrix("events", "vec", None)
        be.upsert("events", "e7", {"vec": pack_f16(_vec(7))})   # dirty now
        be.close()
        be.save_warm()
        be2 = self._boot_without_building()
        keys, _ = be2._vec_cache[("events", "vec")]
        self.assertIn("e7", list(keys))

    def test_a_snapshot_is_read_once(self):
        self.be.save_warm()
        self._boot_without_building()
        self.assertFalse(any((Path(self.dir) / efstore.WARM_DIR).glob("*")))


class TestTeardownSavesOnlyOnAPlannedRestart(unittest.TestCase):
    def _srv(self, be):
        return type("S", (), {"efield": type("F", (), {"b": be})()})()

    def test_no_restart_no_snapshot(self):
        be = mock.Mock()
        with mock.patch.dict(restarts.RESTART, {"requested": False}):
            restarts.save_warm(self._srv(be))
        be.save_warm.assert_not_called()

    def test_a_planned_restart_saves(self):
        be = mock.Mock()
        with mock.patch.dict(restarts.RESTART, {"requested": True}):
            restarts.save_warm(self._srv(be))
        be.save_warm.assert_called_once()

    def test_the_teardown_saves_after_the_last_store_close_and_before_the_reexec(self):
        src = (Path(__file__).parent.parent / "membrane" / "opencode.py").read_text()
        close = src.index('getattr(getattr(srv, "entity_store", None), "close", None)')
        save = src.index("save_warm(srv)")
        reexec = src.index("reexec_if_requested(srv)")
        self.assertLess(close, save)
        self.assertLess(save, reexec)


if __name__ == "__main__":
    unittest.main()


class TestRerankerWarmsBeforeReady(unittest.TestCase):
    """Measured 2026-10-30: the reranker's first predict on mps took 2.4 s, the
    next ones ~1 s. The load runs one full-size predict before it says ready, so
    no turn pays that first one."""

    def test_one_full_batch_runs_before_ready(self):
        import types
        from membrane import recall_rerank as RR
        seen = []

        class _CE:
            def __init__(self, *a, **kw):
                pass

            def predict(self, pairs, **kw):
                seen.append((RR._state, len(pairs)))
                return [0.0] * len(pairs)
        st = types.SimpleNamespace(CrossEncoder=_CE)
        torch = types.SimpleNamespace(backends=types.SimpleNamespace(
            mps=types.SimpleNamespace(is_available=lambda: False)))
        old = (RR._state, RR._model)
        try:
            RR._state, RR._model = "loading", None
            with mock.patch.dict(sys.modules, {"sentence_transformers": st, "torch": torch}), \
                    mock.patch.object(RR, "_find_peer", return_value=None):
                RR._load()
            self.assertEqual(RR._state, "ready")
            self.assertEqual(seen, [("loading", RR.RERANK_TOP)])
        finally:
            RR._state, RR._model = old


class TestSentenceIndexWarmsAtBoot(unittest.TestCase):
    """Measured 2026-10-30: the first Couchbase Lite sentence query after an
    open took 1.07 s, the fourth 0.17 s. warm_vectors on the cold collection
    runs one query in the background instead of building a RAM matrix."""

    def test_warm_vectors_on_sentences_runs_one_query(self):
        from membrane import cblstore
        be = cblstore.CBLiteBackend.__new__(cblstore.CBLiteBackend)
        calls = []
        be.vector_top_k = lambda c, f, v, k, eq=None: calls.append((c, f, k)) or []
        cblstore.CBLiteBackend.warm_vectors(be, "sentences", "vec", wait=True)
        self.assertEqual(calls, [("sentences", "vec", 1)])
