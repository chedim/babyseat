"""OFFLINE MODE (2026-10-11, the operator: "we would take the ThinkPhone running
their membrane seat with me and Gunner on Gunner's daily walks ... the phone
will lose its internet connection for those periods").

Off the boat's network the seat loses the Mac: its model and its embedder.
The scripts and the entity store run anyway; this keeps the rest whole:

  the probe     the backend's /api/version every PROBE_S; FAILS_OFFLINE misses
                = offline, one answer = online again
  the embedder  offline, every OllamaEmbedder in the process embeds on the
                phone (llama-server, the SAME mxbai weights -- measured
                cosine 1.00000 against the Mac), started on the way out
                (when the model file is there and RAM allows), stopped on
                the way back; no local server = embeds fail fast (events
                wait, embedded at home)
  the minds     loop events and llm calls wait (no model to answer them)
  the strikes   a script's failure doesn't count while offline (the HA
                readers would come home disabled)
  the return    a brief to Argus through /v1: when, how long, what the
                field took in, what waited for him

membrane::net carries the state (online/offline, since, which embedder).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request

PROBE_S = 20.0
FAILS_OFFLINE = 2
PROBE_TIMEOUT_S = 4.0
LOCAL_PORT = 8090
LOCAL_MODEL = os.path.expanduser("~/models/mxbai-embed-large-f16.gguf")
LOCAL_MIN_AVAIL_MB = 1200
LOCAL_THREADS = 4
ENT = "net"

STATE = {"offline": False, "since": 0.0, "fails": 0, "local_url": None, "proc": None,
         "embedded_local": 0, "held": 0, "backend": "", "last_probe": 0.0}
_lock = threading.Lock()


def is_offline(srv=None) -> bool:
    return bool(STATE["offline"])


def local_embed_url() -> str | None:
    return STATE["local_url"] if STATE["offline"] else None


def note_held() -> None:
    STATE["held"] += 1


def embed_local(texts: list[str]):
    """Embed on the phone's llama-server (OpenAI shape), L2-normalized like
    ollama's /api/embed. Raises when there is none (offline: fail fast)."""
    import numpy as np
    url = local_embed_url()
    if not url:
        raise RuntimeError("offline: the Mac's embedder is unreachable and no local one is running")
    req = urllib.request.Request(url + "/v1/embeddings", data=json.dumps({"input": list(texts)}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    vecs = np.array([d["embedding"] for d in sorted(data["data"], key=lambda d: d.get("index", 0))], dtype=float)
    n = np.linalg.norm(vecs, axis=1, keepdims=True)
    STATE["embedded_local"] += len(texts)
    return vecs / np.where(n == 0, 1.0, n)


def probe(backend: str) -> bool:
    try:
        urllib.request.urlopen(backend.rstrip("/") + "/api/version", timeout=PROBE_TIMEOUT_S).read()
        return True
    except Exception:
        return False


def _avail_mb() -> int:
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except Exception:
        pass
    return 0


def start_local() -> str | None:
    """The phone's embedder: llama-server on 127.0.0.1:LOCAL_PORT, or None."""
    if STATE["proc"] is not None and STATE["proc"].poll() is None:
        return STATE["local_url"]
    if not os.path.exists(LOCAL_MODEL):
        return None
    if _avail_mb() < LOCAL_MIN_AVAIL_MB:
        return None
    try:
        log = open(os.path.expanduser("~/models/server.log"), "a")
        p = subprocess.Popen(["llama-server", "-m", LOCAL_MODEL, "--embeddings", "--port", str(LOCAL_PORT),
                              "--host", "127.0.0.1", "-t", str(LOCAL_THREADS), "-c", "512", "-b", "512",
                              "-ub", "512"], stdout=log, stderr=log)
    except Exception:
        return None
    url = f"http://127.0.0.1:{LOCAL_PORT}"
    for _ in range(90):
        if p.poll() is not None:
            return None
        try:
            if b"ok" in urllib.request.urlopen(url + "/health", timeout=2).read():
                STATE["proc"] = p
                return url
        except Exception:
            pass
        time.sleep(1)
    p.terminate()
    return None


def stop_local() -> None:
    p = STATE["proc"]
    STATE["proc"] = None
    STATE["local_url"] = None
    if p is not None and p.poll() is None:
        p.terminate()
        try:
            p.wait(10)
        except Exception:
            p.kill()


def _entity(srv):
    from .entities import MEMBRANE_NS, CLASS_CONFIG, QuantumEntity
    est = getattr(srv, "entity_store", None)
    if est is None:
        return None
    ent = est.docs.get(MEMBRANE_NS + ENT)
    if ent is None:
        ent = QuantumEntity(eid=MEMBRANE_NS + ENT, cls=CLASS_CONFIG, properties={
            "name": "net",
            "summary": ("THE SEAT'S CONNECTION to the Mac (its model and embedder): online / offline. "
                        "Offline (a walk): scripts and the entity store run, the field embeds on the phone, "
                        "loop events and model calls wait, script failures don't strike.")})
        est.docs[ent.eid] = ent
    return ent


def _paint(srv) -> None:
    ent = _entity(srv)
    if ent is None:
        return
    pp = ent.properties or {}
    on = not STATE["offline"]
    pp.update({"online": on, "since": round(STATE["since"], 1),
               "embedder": "mac" if on else ("phone" if STATE["local_url"] else "none (waiting)"),
               "state": ("online" if on else
                         f"OFFLINE since {time.strftime('%H:%M', time.localtime(STATE['since']))}: "
                         f"embedding on {'the phone' if STATE['local_url'] else 'nothing (events wait)'}, "
                         f"{STATE['held']} model event(s) waiting")})
    ent.properties = pp


def _note(srv, kind, **kw):
    try:
        from . import seat_panel as SP
        SP.note(srv, kind, **kw)
    except Exception:
        pass


def go_offline(srv, now: float | None = None) -> None:
    with _lock:
        if STATE["offline"]:
            return
        STATE.update({"offline": True, "since": now or time.time(), "held": 0, "embedded_local": 0})
    STATE["local_url"] = start_local()
    _note(srv, "offline", embedder="phone" if STATE["local_url"] else "none")
    _paint(srv)


def go_online(srv, now: float | None = None) -> None:
    with _lock:
        if not STATE["offline"]:
            return
        since = STATE["since"]
        STATE["offline"] = False                 # embeds go back to the Mac first
    stop_local()
    now = now or time.time()
    mins = int((now - since) // 60)
    _note(srv, "online", offline_min=mins, embedded_local=STATE["embedded_local"], held=STATE["held"])
    _paint(srv)
    try:
        from . import restarts as RS
        text = (f"{RS.SIGN} The seat was OFFLINE {time.strftime('%H:%M', time.localtime(since))}–"
                f"{time.strftime('%H:%M', time.localtime(now))} ({mins} min) -- the Mac was unreachable "
                "(a walk, most likely). Meanwhile your scripts and the entity store ran; the field embedded "
                f"{STATE['embedded_local']} sentence(s) on the phone; {STATE['held']} loop event(s) or model call(s) "
                "waited for you and run now. Read what the field took in before you act on old state.")
        threading.Thread(target=RS.post, args=(srv, text), daemon=True, name="notice-online").start()
    except Exception:
        pass


def step(srv, backend: str, now: float | None = None) -> bool:
    """One probe and its transition; returns online."""
    ok = probe(backend)
    STATE["last_probe"] = now or time.time()
    if ok:
        STATE["fails"] = 0
        if STATE["offline"]:
            go_online(srv, now)
    else:
        STATE["fails"] += 1
        if STATE["fails"] >= FAILS_OFFLINE and not STATE["offline"]:
            go_offline(srv, now)
        elif STATE["offline"] and not STATE["local_url"]:
            STATE["local_url"] = start_local()   # RAM may allow it now
            _paint(srv)
    return ok


def start(srv, backend: str) -> None:
    STATE["backend"] = backend
    if os.environ.get("MEMBRANE_OFFLINE_PROBE", "1") == "0":
        return                                   # the suite (tests/conftest.py)

    def loop():
        while True:
            try:
                step(srv, backend)
            except Exception:
                pass
            time.sleep(PROBE_S)
    threading.Thread(target=loop, daemon=True, name="offline-probe").start()
