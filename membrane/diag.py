"""Diagnosing a slow seat without root (#346).

Two instruments, both in-process, both behind localhost-only endpoints on
either HTTP server (:4096 serve, :11500 proxy):

- THE TIMING RING. Every request's route, method, status, duration, thread
  and time go into a bounded ring (TIMING_RING); a request slower than the
  bar (slow_ms) also prints one `[timing] ...` line on stderr. Off by
  default: MEMBRANE_TIMING=1 at launch, or POST /membrane/diag/timing
  {"on": true} at runtime. GET /membrane/diag/timing summarizes per route
  (count, p50, p90, max, total in ms) plus the last few requests.
- THE SAMPLING PROFILER. POST /membrane/diag/profile?seconds=N starts one
  daemon thread that samples sys._current_frames() every SAMPLE_INTERVAL_S
  for N seconds (capped at PROFILE_MAX_S) and counts each thread's stack in
  flamegraph.pl's collapsed form ("thread;file:func:line;... count"). When
  done it writes <data dir>/diag/profile-<ts>.collapsed and keeps the
  result; GET /membrane/diag/profile shows the status, the top PROFILE_TOP
  stacks and the per-thread totals. One profile at a time. It replaces
  `sudo py-spy`.

Neither instrument takes any of the seat's locks: the ring and the profile
result sit behind this module's own lock, held only to copy or append.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import threading
import time
from collections import Counter, deque
from pathlib import Path
from urllib.parse import parse_qs

# -- the constants (#346: a diagnostics buffer, not a behaviour) --------------------

TIMING_RING = 4096            # the requests the ring keeps (oldest drop first)
SLOW_MS_DEFAULT = 1000.0      # the slow-line bar; MEMBRANE_TIMING_SLOW_MS or POST slow_ms sets it
RECENT_SHOWN = 20             # the last requests the summary lists verbatim
SAMPLE_INTERVAL_S = 0.02      # the profiler's tick (50 Hz); ?interval_ms= overrides, 5..1000
PROFILE_DEFAULT_S = 60.0      # POST /membrane/diag/profile without ?seconds=
PROFILE_MAX_S = 120.0         # the cap on ?seconds=
PROFILE_MIN_S = 0.1
PROFILE_TOP = 30              # the stacks GET /membrane/diag/profile lists
PROFILE_MAX_DEPTH = 200       # frames kept per stack (the root side is cut beyond)
PROFILER_THREAD = "membrane-profiler"

DIAG_PATHS = ("/membrane/diag/timing", "/membrane/diag/profile")

_lock = threading.Lock()      # this module's own; never one of the seat's
_ring: deque = deque(maxlen=TIMING_RING)
_on = False
_slow_ms = SLOW_MS_DEFAULT

_prof_thread: threading.Thread | None = None
_prof: dict = {}


def _env_on() -> bool:
    return os.environ.get("MEMBRANE_TIMING", "").strip().lower() in ("1", "true", "yes", "on")


def _env_slow() -> float:
    try:
        return max(0.0, float(os.environ.get("MEMBRANE_TIMING_SLOW_MS", "") or SLOW_MS_DEFAULT))
    except ValueError:
        return SLOW_MS_DEFAULT


def reset() -> None:
    """Back to the launch state: the ring empty, the switch and the bar from
    the environment, no profile result (a running profile finishes on its own)."""
    global _on, _slow_ms, _prof
    with _lock:
        _ring.clear()
        _on = _env_on()
        _slow_ms = _env_slow()
        if _prof_thread is None or not _prof_thread.is_alive():
            _prof = {}


reset()


def _emit(line: str) -> None:
    """The slow line, on stderr (the seat's stderr is SafeStderr, #121)."""
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass


# -- the timing ring ------------------------------------------------------------------

def timing_on() -> bool:
    return _on


def set_timing(on: bool, slow_ms: float | None = None) -> None:
    global _on, _slow_ms
    with _lock:
        _on = bool(on)
        if slow_ms is not None:
            _slow_ms = max(0.0, float(slow_ms))


_ID_SEG = re.compile(r"^[A-Za-z]{2,5}_[A-Za-z0-9]{6,}$")


def route_of(path: str) -> str:
    """The path without its query, ids folded to '*' (a session's id would
    make every session its own route)."""
    p = str(path or "").split("?", 1)[0]
    segs = []
    for s in p.split("/"):
        if _ID_SEG.match(s) or (s.isdigit() and len(s) >= 4) or \
                (len(s) >= 16 and any(c.isdigit() for c in s)):
            s = "*"
        segs.append(s)
    return "/".join(segs)


def record(port, method: str, path: str, status, duration_ms: float, thread: str) -> None:
    """One finished request (a no-op while timing is off)."""
    if not _on:
        return
    row = {"port": port, "method": str(method or ""), "route": route_of(path),
           "status": int(status or 0), "duration_ms": round(float(duration_ms), 1),
           "thread": str(thread or ""), "ts": round(time.time(), 3)}
    with _lock:
        _ring.append(row)
        slow = row["duration_ms"] > _slow_ms
    if slow:
        _emit(f"[timing] {row['port']} {row['method']} {row['route']} {row['status']} "
              f"{row['duration_ms']:.0f}ms thread={row['thread']}")


def _pct(vals: list, p: float) -> float:
    return vals[max(0, math.ceil(p * len(vals)) - 1)]


def summary() -> dict:
    """Per route ("<port> <METHOD> <route>"): count, p50, p90, max, total
    (ms), heaviest total first; the last RECENT_SHOWN requests verbatim."""
    with _lock:
        rows = list(_ring)
        on, slow = _on, _slow_ms
    by: dict = {}
    for r in rows:
        by.setdefault(f"{r['port']} {r['method']} {r['route']}", []).append(r["duration_ms"])
    routes = {}
    for k, v in sorted(by.items(), key=lambda kv: -sum(kv[1])):
        v = sorted(v)
        routes[k] = {"count": len(v), "p50": _pct(v, 0.5), "p90": _pct(v, 0.9),
                     "max": v[-1], "total": round(sum(v), 1)}
    return {"on": on, "slow_ms": slow, "ring": TIMING_RING, "held": len(rows),
            "routes": routes, "recent": rows[-RECENT_SHOWN:]}


class TimedHandler:
    """The mixin both HTTP handlers wear (first in the bases): the clock
    starts once the request line is parsed (never during a keep-alive's idle
    wait) and stops when the request is answered."""

    _diag_t0 = None
    _diag_status = 0

    def parse_request(self):
        ok = super().parse_request()
        if ok and _on:
            self._diag_t0 = time.perf_counter()
            self._diag_status = 0
        return ok

    def send_response(self, code, message=None):
        self._diag_status = code
        return super().send_response(code, message)

    def handle_one_request(self):
        try:
            return super().handle_one_request()
        finally:
            t0, self._diag_t0 = self._diag_t0, None
            if t0 is not None:
                try:
                    port = self.server.server_address[1]
                except Exception:
                    port = 0
                record(port, self.command, self.path, self._diag_status,
                       (time.perf_counter() - t0) * 1000.0, threading.current_thread().name)


# -- the sampling profiler --------------------------------------------------------------

def clamp_seconds(v) -> float:
    try:
        s = float(v)
    except (TypeError, ValueError):
        return PROFILE_DEFAULT_S
    if s != s:
        return PROFILE_DEFAULT_S
    return min(PROFILE_MAX_S, max(PROFILE_MIN_S, s))


_ROOT = str(Path(__file__).resolve().parent.parent) + os.sep
_short_cache: dict = {}


def _short(fn: str) -> str:
    s = _short_cache.get(fn)
    if s is None:
        if fn.startswith(_ROOT):
            s = fn[len(_ROOT):]
        else:
            s = "/".join(fn.replace("\\", "/").split("/")[-2:])
        s = s.replace(";", "_").replace(" ", "_")
        _short_cache[fn] = s
    return s


def _label(name: str) -> str:
    """A thread's name for the stacks: the request threads' numbers folded
    (Thread-* (process_request_thread)), so they group into one tower."""
    name = re.sub(r"^Thread-\d+", "Thread-*", str(name or "?"))
    return name.replace(";", "_").replace("\n", " ")


def _sample(seconds: float, interval: float, out_dir: Path) -> None:
    me = threading.get_ident()
    stacks: Counter = Counter()
    threads: Counter = Counter()
    samples = 0
    end = time.monotonic() + seconds
    while True:
        frames = sys._current_frames()
        names = {t.ident: t.name for t in threading.enumerate()}
        for ident, f in frames.items():
            if ident == me:
                continue
            label = _label(names.get(ident, f"thread-{ident}"))
            parts = []
            while f is not None and len(parts) < PROFILE_MAX_DEPTH:
                c = f.f_code
                parts.append(f"{_short(c.co_filename)}:{c.co_name}:{f.f_lineno}")
                f = f.f_back
            parts.append(label)
            stacks[";".join(reversed(parts))] += 1
            threads[label] += 1
        frames = f = None             # drop the frame references at once
        samples += 1
        with _lock:
            _prof["samples"] = samples
        if time.monotonic() >= end:
            break
        time.sleep(interval)
    path, err = None, None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / f"profile-{time.strftime('%Y%m%d-%H%M%S')}.collapsed"
        p.write_text("".join(f"{k} {n}\n" for k, n in stacks.most_common()))
        path = str(p)
    except OSError as e:
        err = repr(e)
    with _lock:
        _prof.update(running=False, finished=round(time.time(), 3), samples=samples,
                     file=path, error=err, stacks=stacks, threads=dict(threads.most_common()))


def profile_start(seconds=None, interval_ms=None, out_dir=None) -> tuple[bool, str]:
    """Start the one profile: (True, '') or (False, why)."""
    global _prof_thread, _prof
    secs = clamp_seconds(seconds)
    try:
        iv = min(1000.0, max(5.0, float(interval_ms))) / 1000.0 if interval_ms is not None \
            else SAMPLE_INTERVAL_S
    except (TypeError, ValueError):
        iv = SAMPLE_INTERVAL_S
    if out_dir is None:
        from . import settings as _S
        out_dir = _S.data_dir() / "diag"
    with _lock:
        if _prof_thread is not None and _prof_thread.is_alive():
            return False, "a profile is already running"
        _prof = {"running": True, "started": round(time.time(), 3), "seconds": secs,
                 "interval_ms": round(iv * 1000.0, 1), "samples": 0, "file": None, "error": None}
        _prof_thread = threading.Thread(target=_sample, args=(secs, iv, Path(out_dir)),
                                        name=PROFILER_THREAD, daemon=True)
        _prof_thread.start()
    return True, ""


def profile_wait(timeout: float) -> bool:
    """Wait for a running profile to finish (tests); True = none running."""
    t = _prof_thread
    if t is not None and t.is_alive():
        t.join(timeout)
        return not t.is_alive()
    return True


def profile_status() -> dict:
    with _lock:
        p = dict(_prof)
    stacks = p.pop("stacks", None) or Counter()
    out = {"running": bool(p.get("running")), **{k: v for k, v in p.items() if k != "running"}}
    out.setdefault("samples", 0)
    out["top"] = [{"stack": k, "count": n} for k, n in stacks.most_common(PROFILE_TOP)]
    out.setdefault("threads", {})
    if out["running"] and p.get("started"):
        out["elapsed"] = round(time.time() - p["started"], 1)
    return out


# -- the endpoints -------------------------------------------------------------------------

def _send(handler, code: int, obj) -> None:
    b = json.dumps(obj, default=str).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(b)))
    handler.end_headers()
    handler.wfile.write(b)


def _flag(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def route(handler, path: str) -> bool:
    """Answer a /membrane/diag/* request (True), or leave it (False).
    Localhost only (seatauth.is_loopback: the socket's peer, never a header)."""
    if path not in DIAG_PATHS:
        return False
    from . import seatauth as _SA
    if not _SA.is_loopback(handler):
        _send(handler, 403, {"type": "error", "error": {"message": "diagnostics are localhost-only"}})
        return True
    q = {k: v[-1] for k, v in parse_qs(handler.path.split("?", 1)[1] if "?" in handler.path else "").items()}
    post = handler.command == "POST"
    body = {}
    if post:
        try:
            n = int(handler.headers.get("Content-Length", 0) or 0)
            raw = handler.rfile.read(n) if n else b""
            body = json.loads(raw) if raw else {}
        except (ValueError, OSError):
            body = {}
        if not isinstance(body, dict):
            body = {}
    if path == "/membrane/diag/timing":
        if post:
            on = body.get("on", q.get("on"))
            slow = body.get("slow_ms", q.get("slow_ms"))
            try:
                slow = None if slow is None else float(slow)
            except (TypeError, ValueError):
                slow = None
            if body.get("reset") or _flag(q.get("reset", "")):
                with _lock:
                    _ring.clear()
            set_timing(_on if on is None else (on if isinstance(on, bool) else _flag(on)), slow)
        _send(handler, 200, summary())
        return True
    if post:
        ok, why = profile_start(body.get("seconds", q.get("seconds")),
                                body.get("interval_ms", q.get("interval_ms")))
        if not ok:
            _send(handler, 409, {"type": "error", "error": {"message": why}, **profile_status()})
            return True
    _send(handler, 200, profile_status())
    return True
