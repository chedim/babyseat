"""membrane/recall_rerank.py: the reranker over the banner's top rows (#680).

bge-reranker-v2-m3 (BAAI, Apache-2.0) reads the message and a row's answer
together and scores how well the row answers it; that score is the scorer's
`bge` feature inside the scorer's top RERANK_TOP rows (recall_banner.rank).
Measured on LoCoMo 0-1: hit@8 169 -> 178 over the top 20, ~0.25 s a call warm
on the Mac's GPU, ~3.3 GB of GPU memory while loaded (fp32; fp16 and bf16
scored 173 and saved no memory as loaded).

Optional: on where `sentence-transformers` is installed (the `rerank` extra),
off with MEMBRANE_RERANK=0. The seat starts the load in the background at
boot, right after the field is up (warm()); until the model is ready, or
without it, a call returns None and the banner keeps the scorer's order.
Once loaded it stays loaded (operator, 2026-10-29: no release). Calls are
serialized: one model, one device."""

from __future__ import annotations

import os
import sys
import threading

MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_TOP = 20           # rows reranked: the scorer's top 20 (operator-approved)
MAX_LEN = 128             # tokens per (message, answer) pair (operator-approved): LoCoMo
                          # 179 at 128, 256 and 512; 20 long seat rows 0.42 s at 128, 1.72 s at 512

_lock = threading.Lock()  # the state
_run = threading.Lock()   # one predict at a time
_state = "idle"           # idle | loading | ready | off
_model = None
_said = False


def _installed() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def _say(msg: str) -> None:
    global _said
    if not _said:
        _said = True
        print(f"[rerank] {msg}", file=sys.stderr)


def _load() -> None:
    global _state, _model
    try:
        import torch
        from sentence_transformers import CrossEncoder
        dev = "mps" if torch.backends.mps.is_available() else "cpu"
        m = CrossEncoder(MODEL, max_length=MAX_LEN, device=dev)   # fp32: fp16/bf16 lost 5 of the 9 points
        with _lock:
            _model, _state = m, "ready"
        print(f"[rerank] {MODEL} ready on {dev}", file=sys.stderr)
    except Exception as e:                       # never breaks recall
        with _lock:
            _state = "off"
        print(f"[rerank] off: {MODEL} did not load ({e})", file=sys.stderr)


def _start() -> None:
    threading.Thread(target=_load, name="rerank-load", daemon=True).start()


def _wanted() -> bool:
    if os.environ.get("MEMBRANE_RERANK", "1") == "0":
        return False
    if not _installed():
        _say("off: sentence-transformers is not installed (pip install 'babyseat[rerank]')")
        return False
    return True


def _kick() -> None:
    global _state
    with _lock:
        if _state == "idle":
            _state = "loading"
            _start()


def warm() -> None:
    """Start the load now (the seat calls it at boot, right after the field
    is up), so the first topical turn after a restart does not pay for it."""
    if _wanted():
        _kick()


def scores(text: str, docs: list[str]) -> list[float] | None:
    """One score per doc, or None (off, not installed, still loading, failed)."""
    if not docs or not _wanted():
        return None
    _kick()
    with _lock:
        if _state != "ready":
            return None
        m = _model
    try:
        with _run:
            out = m.predict([(text, d) for d in docs], batch_size=len(docs), show_progress_bar=False)
        return [float(x) for x in out]
    except Exception as e:
        print(f"[rerank] failed ({e})", file=sys.stderr)
        return None
