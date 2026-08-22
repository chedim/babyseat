"""Local Ollama embedder -- mirrors rosetta-check's OllamaCodeEmbedder seam exactly.

POST {base}/api/embed  {"model":..., "input":[text,...]}  ->  "embeddings": [[...], ...]

Local + private by construction (default http://localhost:11434); only used when
explicitly called. The lab's toy experiments run without it (synthetic vectors);
this module is the bridge to real mxbai-embed-large (1024-d) embeddings.
Uses only stdlib (urllib) + numpy, so the project stays a single pip install (numpy).
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error

import numpy as np

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "mxbai-embed-large"


class OllamaEmbedder:
    def __init__(self, base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def embed(self, texts) -> np.ndarray:
        """Embed a list of strings -> (N, D) float array. One /api/embed batch."""
        texts = list(texts)
        if not texts:
            return np.zeros((0, 0), dtype=float)
        body = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/embed",
            data=body,
            headers={"Content-Type": "application/json"},
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