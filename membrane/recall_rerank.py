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
serialized: one model, one device.

ONE COPY PER MACHINE (#683): torch's GPU memory belongs to the process that
loaded it, so the seat that has the model loaded SERVES it to the other seats
on the machine (POST /membrane/rerank -> serve()). A seat that needs the
reranker first asks the same-host seats from the peer roster (the load
thread, never a turn); the first that answers ready is called instead of
loading a copy, until a call to it fails. With no serving seat the seat loads
it as before and from then on serves. If the serving seat is down or a call
fails, that call returns None (the scorer's order) and the next call asks
again, then loads. MEMBRANE_RERANK=0: neither serving nor calling."""

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
_peer: int | None = None   # the port of the same-host seat that serves the model (state "remote")
_peers_fn = None           # () -> the live peer rows (the seat sets it in warm())


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


def _post(port: int, payload: dict, timeout: float) -> dict:
    """POST /membrane/rerank on a same-host seat (the tests swap it)."""
    import json
    import urllib.request
    from . import jwtauth
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/membrane/rerank", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **jwtauth.internal_headers()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _timeout() -> float:
    from .global_field import PEER_ASK_TIMEOUT      # the peer lanes' own bound
    return PEER_ASK_TIMEOUT


def _find_peer() -> int | None:
    """The port of the first same-host seat whose model is ready, or None."""
    try:
        rows = list(_peers_fn()) if _peers_fn else []
    except Exception:
        return None
    for r in rows:
        try:
            port = int(r.get("port") or 0)
            if port and _post(port, {"text": "", "docs": []}, _timeout()).get("scores") is not None:
                return port
        except Exception:
            continue
    return None


def _load() -> None:
    global _state, _model, _peer
    port = _find_peer()                           # one copy per machine (#683)
    if port is not None:
        with _lock:
            _peer, _state = port, "remote"
        print(f"[rerank] served by the seat on :{port}", file=sys.stderr)
        return
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


def warm(peers=None) -> None:
    """Start the load now (the seat calls it at boot, right after the field
    is up), so the first topical turn after a restart does not pay for it.
    `peers` returns the live peer rows (a serving seat is used, not loaded)."""
    global _peers_fn
    if peers is not None:
        _peers_fn = peers
    if _wanted():
        _kick()


def _predict(m, text: str, docs: list[str]) -> list[float]:
    with _run:
        out = m.predict([(text, d) for d in docs], batch_size=len(docs), show_progress_bar=False)
    return [float(x) for x in out]


def serve(text: str, docs: list[str]) -> list[float] | None:
    """What the seat answers another seat (POST /membrane/rerank): scores
    from the LOCAL model, or None when it is not ready. Never loads, never
    calls on: a seat served by a peer does not serve."""
    if os.environ.get("MEMBRANE_RERANK", "1") == "0":
        return None
    with _lock:
        m = _model if _state == "ready" else None
    if m is None:
        return None
    if not docs:
        return []
    try:
        return _predict(m, text, docs)
    except Exception as e:
        print(f"[rerank] serve failed ({e})", file=sys.stderr)
        return None


def scores(text: str, docs: list[str]) -> list[float] | None:
    """One score per doc, or None (off, not installed, still loading, failed,
    the serving seat down)."""
    global _state, _peer
    if not docs or not _wanted():
        return None
    _kick()
    with _lock:
        state, m, port = _state, _model, _peer
    if state == "remote":
        try:
            got = _post(port, {"text": text, "docs": docs}, _timeout()).get("scores")
            if got is not None and len(got) == len(docs):
                return [float(x) for x in got]
        except Exception as e:
            print(f"[rerank] the serving seat failed ({e})", file=sys.stderr)
        with _lock:                                # ask again next call, then load
            if _state == "remote" and _peer == port:
                _state, _peer = "idle", None
        return None
    if state != "ready":
        return None
    try:
        return _predict(m, text, docs)
    except Exception as e:
        print(f"[rerank] failed ({e})", file=sys.stderr)
        return None
