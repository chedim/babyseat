"""The seat's lap timer (#1115): the teardown's per-step `[teardown] <step> Ns` lines (2026-11-02,
the 90 s hang) and the boot's one `[boot]` summary line come from the same few lines.

- teardown: `mark(step)` after each step -> one line per step (echo);
- boot: `step(name)` as each step starts -> `done()` returns the summary line, and `now` names the
  step running (what the booting serve port answers while the seat is not up yet).
"""
from __future__ import annotations

import time


class Laps:
    def __init__(self, tag: str, clock=time.monotonic, echo=None, cpu=None):
        """`cpu`: a second clock (the boot passes time.thread_time: its own thread's CPU time),
        shown beside each lap -- wall far above cpu = the step waited (other threads, the disk)."""
        self.tag, self.clock, self.echo, self.cpu = tag, clock, echo, cpu
        self.t0 = self.t = clock()
        self.c = cpu() if cpu else 0.0
        self.laps: list[tuple[str, float]] = []
        self.cpus: list[float] = []
        self.now: str | None = None

    def mark(self, step: str) -> float:
        """The step that just ended (the time since the last mark)."""
        t = self.clock()
        dt = t - self.t
        self.t = t
        if self.cpu:
            c = self.cpu()
            self.cpus.append(c - self.c)
            self.c = c
        self.laps.append((step, dt))
        if self.echo is not None:
            print(f"[{self.tag}] {step} {dt:.1f}s", file=self.echo, flush=True)
        return dt

    def step(self, name: str) -> None:
        """A step starts; the one running ends under its own name ('start' before the first)."""
        if self.now is not None or self.clock() > self.t:
            self.mark(self.now or "start")
        self.now = name

    def elapsed(self) -> float:
        return self.clock() - self.t0

    def done(self) -> str:
        """The running step ends; the summary line."""
        if self.now is not None:
            self.mark(self.now)
            self.now = None
        steps = ", ".join(f"{s} {dt:.1f}s" + (f" (cpu {self.cpus[i]:.1f}s)" if self.cpu else "")
                          for i, (s, dt) in enumerate(self.laps))
        return f"[{self.tag}] {self.t - self.t0:.1f}s: {steps}"


REFRESH_S = 2      # the loading page reloads itself, and Retry-After, every this many seconds (FLAGGED: awaits the operator)

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}"><title>membrane: starting</title>
<style>body{{font:16px system-ui,sans-serif;margin:3em auto;max-width:32em;padding:0 16px;
color:#222;background:#fafafa}}@media (prefers-color-scheme:dark){{body{{color:#ddd;background:#111}}}}
p{{opacity:.7}}</style></head><body><h1>loading the field&hellip;</h1>
<p>step: {step} ({elapsed:.0f}s since start)</p>
<p>This page reloads itself; the seat opens when its boot is done.</p></body></html>"""


class BootDoor:
    """The serve port's answer while the seat boots (#1115). The boot reserves the serve port at its
    top (bound + listening) and the native serve adopts that socket once everything is up; in between
    the door answers on it: 503 + Retry-After for every request, with the step the boot is on
    (`laps.now`) -- a page that reloads itself for a browser, JSON for the rest. Never 200: nothing
    reads a half-booted seat as ready (Home Assistant's readiness is /global/health == 200).
    `handover()` stops answering and leaves the socket open for the real server (connections that
    arrive in between wait in its backlog)."""

    def __init__(self, sock, laps: Laps):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        door = self

        class _H(BaseHTTPRequestHandler):
            def _loading(self):
                import html
                import json
                step = door.laps.now or "starting"
                if "text/html" in (self.headers.get("Accept") or ""):
                    body = _PAGE.format(refresh=REFRESH_S, step=html.escape(step),
                                        elapsed=door.laps.elapsed()).encode()
                    ctype = "text/html; charset=utf-8"
                else:
                    body = json.dumps({"loading": True, "step": step,
                                       "elapsed_s": round(door.laps.elapsed(), 1)}).encode()
                    ctype = "application/json"
                self.send_response(503)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Retry-After", str(REFRESH_S))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _loading

            def log_message(self, *a):
                pass

        self.laps = laps
        self.httpd = ThreadingHTTPServer(sock.getsockname()[:2], _H, bind_and_activate=False)
        self.httpd.socket = sock          # the reservation: answered here, never rebound
        self.httpd.daemon_threads = True
        self._th = threading.Thread(target=self.httpd.serve_forever, name="boot-door", daemon=True)
        self._th.start()

    def handover(self) -> None:
        """Stop answering; the socket stays open for the native serve to adopt. Idempotent."""
        if self._th is None:
            return
        self.httpd.shutdown()
        self._th = None
