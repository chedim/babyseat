"""membrane/recall_rerank.py: the reranker over the banner's top rows (#680).

bge-reranker-v2-m3 (BAAI, Apache-2.0) reads the message and a row's answer
together and scores how well the row answers it; that score is the scorer's
`bge` feature inside the scorer's top RERANK_TOP rows (recall_banner.rank).
Measured on LoCoMo 0-1: hit@8 169 -> 178 over the top 20, ~0.25 s a call warm
on the Mac's GPU, ~3.3 GB of GPU memory while loaded (fp32; fp16 and bf16
scored 173 and saved no memory as loaded).

On by default (operator, 2026-11-11): when `sentence-transformers` is missing the
seat installs it at boot on the load thread (ensure()), the way living language
installs spaCy -- wheels only; on Linux without an NVIDIA driver torch comes from
the CPU-only index first. No install where it would hurt (may_install()): Home
Docker (MEMBRANE_NO_AUTOINSTALL, unless MEMBRANE_RERANK=1), musl (no torch wheels:
Home Assistant OS / Container), under MIN_RAM_GB of RAM. Home Assistant on glibc
(Core, proot Debian) installs it under HA's own package_constraints.txt. The `rerank` extra still
installs it up front. Off with MEMBRANE_RERANK=0. The seat starts the load in the background at
boot, once the boot is otherwise done (warm(); #1115), or on the first recall before that; until the model is ready, or
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
MIN_RAM_GB = 8            # no auto-install below this much RAM (FLAGGED: awaits the operator)
CPU_TORCH = "https://download.pytorch.org/whl/cpu"
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
    except ValueError:                           # loaded with no spec: it is there
        return "sentence_transformers" in sys.modules
    except ImportError:
        return False


def _libc() -> tuple:
    import platform
    return platform.libc_ver()


def _ram_gb() -> float:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
    except (ValueError, OSError, AttributeError):
        return 0.0


def _ha_constraints() -> str | None:
    """Home Assistant's own pins (homeassistant/package_constraints.txt) when this Python
    has HA: the install keeps every package HA pins, the way HA installs its requirements."""
    import importlib.util
    try:
        spec = importlib.util.find_spec("homeassistant")
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    p = os.path.join(list(spec.submodule_search_locations)[0], "package_constraints.txt")
    return p if os.path.exists(p) else None


def _nvidia() -> bool:
    return os.path.exists("/proc/driver/nvidia") or os.path.exists("/dev/nvidia0")


def may_install() -> tuple[bool, str]:
    """(may the seat install the reranker here, why not)."""
    if (os.environ.get("MEMBRANE_NO_AUTOINSTALL") and os.environ.get("MEMBRANE_RERANK") != "1"
            and _ha_constraints() is None):
        return False, "installs are off here (MEMBRANE_NO_AUTOINSTALL; MEMBRANE_RERANK=1 overrides)"
    if sys.platform.startswith("linux") and _libc()[0] != "glibc":
        return False, "musl (torch has no wheels for it)"
    ram = _ram_gb()
    if ram and ram < MIN_RAM_GB:
        return False, f"{ram:.0f} GB RAM (under {MIN_RAM_GB} GB)"
    return True, ""


def ensure(log=None) -> bool:
    """sentence-transformers installed (or installed now, wheels only). Runs on the load
    thread, never a turn. A failure logs one line and leaves the reranker off."""
    import importlib
    import subprocess
    log = log or (lambda m: print(m, file=sys.stderr))
    if _installed():
        return True
    ok, why = may_install()
    if not ok:
        log(f"[rerank] off: sentence-transformers is not installed, and not installed here: {why}")
        return False
    pip = [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--only-binary=:all:"]
    ha = _ha_constraints()
    if ha:
        pip += ["-c", ha]
    steps = []
    if sys.platform.startswith("linux") and not _nvidia():
        steps.append(pip + ["--index-url", CPU_TORCH, "torch"])
    steps.append(pip + ["sentence-transformers"])
    log("[rerank] installing sentence-transformers (the recall reranker) in the background")
    for cmd in steps:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except Exception as e:
            log(f"[rerank] off: install failed ({type(e).__name__})")
            return False
        if r.returncode != 0:
            tail = ((r.stderr or r.stdout or "").strip().splitlines() or ["?"])[-1]
            log(f"[rerank] off: install failed ({tail})")
            return False
    importlib.invalidate_caches()
    return _installed()


def _say(msg: str) -> None:
    global _said
    if not _said:
        _said = True
        print(f"[rerank] {msg}", file=sys.stderr)


def _post(port: int, payload: dict, timeout: float) -> dict:
    """POST /membrane/rerank on a same-host seat (the tests swap it)."""
    import json
    import urllib.request
    from . import jwtauth, seatauth
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/membrane/rerank", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **seatauth.loopback_headers(jwtauth.internal_headers())})
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



def _device(torch) -> str:
    """Where the reranker runs: a CUDA device when PyTorch reports one (NVIDIA, or an AMD GPU through a ROCm build,
    which PyTorch exposes as `cuda`), else Apple's MPS, else the CPU."""
    cuda = getattr(torch, "cuda", None)
    if cuda is not None and cuda.is_available():
        return "cuda"
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"

def _load() -> None:
    global _state, _model, _peer
    port = _find_peer()                           # one copy per machine (#683)
    if port is not None:
        with _lock:
            _peer, _state = port, "remote"
        print(f"[rerank] served by the seat on :{port}", file=sys.stderr)
        return
    if not _installed() and not ensure():
        with _lock:
            _state = "off"
        return
    try:
        import torch
        from sentence_transformers import CrossEncoder
        dev = _device(torch)
        m = CrossEncoder(MODEL, max_length=MAX_LEN, device=dev)   # fp32: fp16/bf16 lost 5 of the 9 points
        # #720: the first predict on a fresh model pays its kernel set-up (2.4 s on
        # mps vs ~1 s after): one full-size batch here, so no turn pays it
        m.predict([("warm", "warm " * (MAX_LEN // 2))] * RERANK_TOP, show_progress_bar=False)
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
        ok, why = may_install()
        if not ok:
            _say(f"off: sentence-transformers is not installed ({why}; pip install 'babyseat[rerank]')")
        return ok
    return True


def _kick() -> None:
    global _state
    with _lock:
        if _state == "idle":
            _state = "loading"
            _start()


def warm(peers=None) -> None:
    """Start the load now (the seat calls it once its boot is done, #1115 -- the field
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
