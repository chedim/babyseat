"""Local Ollama embedder -- mirrors rosetta-check's OllamaCodeEmbedder seam exactly.

POST {base}/api/embed  {"model":..., "input":[text,...]}  ->  "embeddings": [[...], ...]

Local + private by construction (default http://localhost:11434); only used when
explicitly called. The lab's toy experiments run without it (synthetic vectors);
this module is the bridge to real mxbai-embed-large (1024-d) embeddings.
Uses only stdlib (urllib) + numpy, so the project stays a single pip install (numpy).
"""

from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error

import numpy as np

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "mxbai-embed-large"

# Same-turn dedup cache. One user turn is routinely embedded 2-3 times on the
# hot path (semantic intercept in opencode.py, the gate in runtime.py, dedup
# scans) -- identical (model, text) pairs, identical vectors. A tiny LRU of
# recent embeddings removes the redundant /api/embed round-trips without
# changing any geometry: callers receive a fresh COPY each time (the veil
# nudge perturbs vectors, and a shared array would corrupt the cache).
EMBED_CACHE_MAX = 64


class OllamaEmbedder:
    def __init__(self, base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL,
                 timeout: int = 120, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.token = token        # optional bearer token; in-memory only, never persisted
        self._cache: dict[tuple[str, str], np.ndarray] = {}
        self._cache_lock = threading.Lock()

    # --- embed cache (same-turn dedup; small LRU, thread-safe) ------------
    def _cache_get(self, key: tuple[str, str]) -> np.ndarray | None:
        with self._cache_lock:
            vec = self._cache.get(key)
            if vec is None:
                return None
            # refresh LRU position (re-insert moves the key to the end).
            del self._cache[key]
            self._cache[key] = vec
            return vec.copy()

    def _cache_put(self, key: tuple[str, str], vec: np.ndarray) -> None:
        with self._cache_lock:
            self._cache[key] = vec.copy()
            while len(self._cache) > EMBED_CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))

    def _cache_clear(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def embed(self, texts) -> np.ndarray:
        """Embed a list of strings -> (N, D) float array.

        One /api/embed batch for the misses; cache hits are answered locally.
        The batch shape is preserved on return."""
        texts = list(texts)
        if not texts:
            return np.zeros((0, 0), dtype=float)
        keyed = [(self.model, t) for t in texts]
        cached = [self._cache_get(k) for k in keyed]
        missing_idx = [i for i, v in enumerate(cached) if v is None]
        if missing_idx:
            # dedup WITHIN the batch too: identical texts fetch once.
            uniq: dict[str, np.ndarray] = {}
            for i in missing_idx:
                uniq.setdefault(texts[i], None)
            miss_texts = list(uniq)
            vecs = self._embed_remote(miss_texts)
            for text, v in zip(miss_texts, vecs):
                uniq[text] = v
            for i in missing_idx:
                v = uniq[texts[i]]
                cached[i] = v
                self._cache_put(keyed[i], v)
        return np.array([c for c in cached], dtype=float)

    def _embed_remote(self, texts: list[str]) -> list[np.ndarray]:
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(
            self.base_url + "/api/embed",
            data=body,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama /api/embed failed (is Ollama running at {self.base_url} "
                f"with model '{self.model}'?): {e}"
            ) from e
        vecs = data.get("embeddings")
        if not vecs:
            raise RuntimeError(f"Ollama response missing 'embeddings': {str(data)[:200]}")
        return np.array(vecs, dtype=float)