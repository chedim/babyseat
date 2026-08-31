"""P0a: the embedder's same-turn LRU cache.

One user turn is routinely embedded 2-3 times on the hot path (semantic
intercept, the gate, dedup scans). The cache must:
- dedup identical (model, text) pairs within one embed() batch AND across
  calls, with the remote transport hit exactly once;
- return fresh copies each time (callers nudge vectors in place -- a shared
  array would corrupt the cache);
- keep batch shape and ordering (hits and misses interleaved);
- evict LRU beyond EMBED_CACHE_MAX;
- never cache across models (same text, different model = different space);
- degrade to the plain path on remote failure (no caching of failures).
"""

import sys
import threading
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from membrane import embed as E


class _Transport:
    """Fake /api/embed: deterministic hash vectors + a call counter."""

    def __init__(self, dim=8):
        self.dim = dim
        self.calls: list[list[str]] = []
        self.lock = threading.Lock()
        self.fail_next = False

    def __call__(self, embedder, texts):
        with self.lock:
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("transport down")
            self.calls.append(list(texts))
        out = []
        for t in texts:
            v = np.zeros(self.dim)
            for i, w in enumerate((t or "").split()):
                v[i % self.dim] += 1.0 + (hash((w, i)) % 97) / 97.0
            n = float(np.linalg.norm(v))
            out.append(v / n if n > 1e-12 else v)
        return np.array(out, dtype=float)


class TestEmbedCache(unittest.TestCase):
    def _emb(self, transport):
        emb = E.OllamaEmbedder(base_url="http://fake", model="m-test")
        emb._embed_remote = lambda texts: transport(emb, texts)
        return emb

    def test_same_batch_dedup_hits_transport_once_per_unique_text(self):
        t = _Transport()
        emb = self._emb(t)
        vecs = emb.embed(["alpha beta", "alpha beta", "gamma"])
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(t.calls[0], ["alpha beta", "gamma"])  # miss list only
        self.assertEqual(vecs.shape, (3, 8))
        np.testing.assert_allclose(vecs[0], vecs[1])

    def test_cross_call_hit_skips_transport(self):
        t = _Transport()
        emb = self._emb(t)
        emb.embed(["run the tests"])
        self.assertEqual(len(t.calls), 1)
        emb.embed(["run the tests"])
        self.assertEqual(len(t.calls), 1)  # served from cache
        v2 = emb.embed(["run the tests"])
        self.assertEqual(v2.shape, (1, 8))

    def test_returns_fresh_copies(self):
        t = _Transport()
        emb = self._emb(t)
        v1 = emb.embed(["nudge me"])[0]
        v1[0] = 99.0                     # caller mutates (the veil nudge)
        v2 = emb.embed(["nudge me"])[0]
        self.assertNotEqual(v2[0], 99.0)
        np.testing.assert_allclose(np.linalg.norm(v2), 1.0, rtol=1e-9)

    def test_model_scoped_keys(self):
        t = _Transport()
        emb = self._emb(t)
        emb.embed(["same text"])
        emb.model = "m-other"
        emb.embed(["same text"])
        self.assertEqual(len(t.calls), 2)  # different space, no reuse

    def test_batch_shape_and_order_preserved(self):
        t = _Transport()
        emb = self._emb(t)
        a = emb.embed(["x one", "y two"])
        b = emb.embed(["y two", "x one", "z three"])
        self.assertEqual(b.shape, (3, 8))
        np.testing.assert_allclose(b[0], a[1])   # y two
        np.testing.assert_allclose(b[1], a[0])   # x one

    def test_lru_eviction(self):
        t = _Transport()
        emb = self._emb(t)
        emb.embed([f"seed {i}" for i in range(E.EMBED_CACHE_MAX)])
        self.assertEqual(len(emb._cache), E.EMBED_CACHE_MAX)
        emb.embed(["seed 0"])            # refresh LRU position (cache hit)
        self.assertEqual(len(t.calls), 1)
        emb.embed(["overflow a"])        # evicts the oldest -- NOT seed 0
        emb.embed(["seed 0"])
        self.assertEqual(len(t.calls), 2)
        self.assertNotIn("seed 0", t.calls[1])
        self.assertIn("seed 0", t.calls[0])

    def test_failure_not_cached(self):
        t = _Transport()
        emb = self._emb(t)
        t.fail_next = True
        with self.assertRaises(RuntimeError):
            emb.embed(["doomed"])
        self.assertEqual(t.calls, [])    # failed before recording
        t.fail_next = False
        emb.embed(["doomed"])            # retried, now cached
        self.assertEqual(len(t.calls), 1)
        emb.embed(["doomed"])            # served from cache
        self.assertEqual(len(t.calls), 1)

    def test_thread_safety_smoke(self):
        t = _Transport()
        emb = self._emb(t)
        errs: list[Exception] = []

        def worker(i):
            try:
                for _ in range(50):
                    emb.embed([f"text {i % 4}", f"shared {i % 3}"])
            except Exception as e:   # noqa: BLE001
                errs.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()