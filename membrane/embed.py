"""Local Ollama embedder -- mirrors rosetta-check's OllamaCodeEmbedder seam exactly.

POST {base}/api/embed  {"model":..., "input":[text,...]}  ->  "embeddings": [[...], ...]

Local + private by construction (default http://localhost:11434); only used when
explicitly called. The lab's toy experiments run without it (synthetic vectors);
this module is the bridge to real mxbai-embed-large (1024-d) embeddings.
Uses only stdlib (urllib) + numpy, so the project stays a single pip install (numpy).
"""

from __future__ import annotations

import json
import os
import threading
import time
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
        # cold-start reality on edge hardware (the agdroid): the FIRST embed
        # after model load from flash measured ~28-30s; under proot/flash
        # pressure it can exceed 120s. Env override exists for exactly this:
        # MEMBRANE_EMBED_TIMEOUT=300 on the seat. Steady-state is ~50-300ms.
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = int(os.environ.get("MEMBRANE_EMBED_TIMEOUT",
                                          str(timeout)))
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

    # THE DUTY PACE (the operator's ruling: 'learn slower, not hot CPU'):
    # a big backfill arrives as ONE list -- send it in CHUNKS with a
    # breathing pause between them so the ollama core never pegs a whole
    # core for the whole batch. Single-text query embeds stay instant
    # (one chunk, no pause). Knobs: MEMBRANE_EMBED_CHUNK (default 32),
    # MEMBRANE_EMBED_PACE_S (default 0.25, 0 disables).
    _CHUNK_DEFAULT = 32
    _PACE_DEFAULT_S = 0.25

    def _embed_remote(self, texts: list[str]) -> list[np.ndarray]:
        if len(texts) <= self._CHUNK_DEFAULT:
            return self._embed_chunk(texts)
        pace = float(os.environ.get("MEMBRANE_EMBED_PACE_S",
                                    str(self._PACE_DEFAULT_S)))
        chunk_n = max(1, int(os.environ.get("MEMBRANE_EMBED_CHUNK",
                                            str(self._CHUNK_DEFAULT))))
        out: list[np.ndarray] = []
        for i in range(0, len(texts), chunk_n):
            if out and pace > 0:
                time.sleep(pace)
            out.extend(self._embed_chunk(texts[i:i + chunk_n]))
        return out

    def _embed_chunk(self, texts: list[str]) -> list[np.ndarray]:
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