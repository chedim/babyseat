"""The seat's lap timer (#1115): the teardown's per-step `[teardown] <step> Ns` lines (2026-11-02,
the 90 s hang) and the boot's one `[boot]` summary line come from the same few lines.

- teardown: `mark(step)` after each step -> one line per step (echo);
- boot: `step(name)` as each step starts -> `done()` returns the summary line, and `now` names the
  step running (what the booting serve port answers while the seat is not up yet).
"""
from __future__ import annotations

import time
from pathlib import Path


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


REFRESH_S = 2      # the page polls the door, and Retry-After, every this many seconds (operator-approved 2026-11-11)
SHAPE_POINTS = 3000   # the loading page's field: the heaviest this many entities (FLAGGED: awaits the operator)
SHAPE_EDGES = 3       # ... and up to this many of each one's strongest relations (FLAGGED: awaits the operator)

# what the boot has loaded so far, for the loading page: the field's hot rows (all, and the entity
# states among them -- the shape's points appear in their load order). Written by the store's load.
PROGRESS: dict = {}


def field_shape(srv) -> dict | None:
    """#1115: the field's SHAPE for the loading page, numbers only (the door answers without auth: no
    eids, names or words): the field view's own 3-D layout (lanes.field3d_projection, the default
    axes) of the heaviest SHAPE_POINTS entities -- p: x,y,z per point in 0..1000; m: mass 0..255
    (the heaviest = 255), heaviest first; o: when its entity state loads, 0..1000 of the rows; e: index
    pairs, each point's SHAPE_EDGES strongest relations among the kept points."""
    from . import lanes
    proj = lanes.field3d_projection(srv)
    n = len(proj.ids)
    if not n:
        return None
    rows = getattr(getattr(srv.efield, "b", None), "colls", {}).get("entity_states") or {}
    rank: dict = {}
    for i, d in enumerate(list(rows.values())):     # the RAM order IS the load order (ORDER BY o)
        e = d.get("eid") if isinstance(d, dict) else None
        if e is not None and e not in rank:
            rank[e] = i
    tot = max(1, len(rows))
    keep = sorted(range(n), key=lambda i: (-float(proj.mass[i]), i))[:SHAPE_POINTS]
    pos = {i: j for j, i in enumerate(keep)}
    mx = max(1.0, float(proj.mass[keep[0]]))

    def clip(x):
        return int(round(1000 * min(1.0, max(0.0, float(x)))))
    p = [clip(proj.coords[i][k]) for i in keep for k in range(3)]
    m = [int(round(255 * max(0.0, float(proj.mass[i])) / mx)) for i in keep]
    o = [int(round(1000 * min(rank.get(proj.ids[i], tot), tot) / tot)) for i in keep]
    deg = [0] * len(keep)
    e: list = []
    for s_, t_, _rel, _cnt in proj.edges:           # strongest first
        a, b = pos.get(s_), pos.get(t_)
        if a is None or b is None or a == b or deg[a] >= SHAPE_EDGES or deg[b] >= SHAPE_EDGES:
            continue
        deg[a] += 1
        deg[b] += 1
        e += [a, b]
    return {"v": 1, "n": len(keep), "p": p, "m": m, "o": o, "e": e}


SHAPE_FILE = "shape.json"


def save_shape(srv, d) -> int:
    """The shape into the warm snapshot's folder `d` (the clean stop); the points saved."""
    import json
    from pathlib import Path
    shape = field_shape(srv)
    if not shape:
        return 0
    (Path(d) / SHAPE_FILE).write_text(json.dumps(shape, separators=(",", ":")))
    return shape["n"]


def read_shape(field_path: str) -> bytes | None:
    """The last clean stop's shape, found from the field path before the store opens (the boot's
    door starts first; the store's open consumes the warm folder)."""
    from pathlib import Path
    try:
        from .efstore import WARM_DIR, _field_dirname_for
        j = Path(field_path).resolve().parent / _field_dirname_for(field_path)
        for d in (j.with_name(j.name + ".sqlite"), j.with_name(j.name + ".cblite"), j):
            f = d / WARM_DIR / SHAPE_FILE
            if f.is_file():
                return f.read_bytes()
    except Exception:
        return None
    return None


def status(laps: Laps, shape) -> dict:
    """What the door answers a poll: the step, the seconds, the load counts, whether recall's models
    are up (the embedder step done) and whether a shape is there."""
    done = [s for s, _ in laps.laps]
    return {"loading": True, "step": laps.now or "starting", "elapsed_s": round(laps.elapsed(), 1),
            "steps": len(done), "recall": "embedder" in done, "shape": bool(shape),
            **{k: int(PROGRESS.get(k) or 0) for k in ("rows", "rows_total", "states", "states_total")}}


# the field animation, ONE source (#1134): this file is embedded inline in the page below and loaded by
# the UI with a <script> tag (membrane/ui/index.html)
ANIM_JS = (Path(__file__).resolve().parent / "ui" / "fieldanim.js").read_text()


def page_html(laps: Laps) -> str:
    import html
    return (_PAGE.replace("__ANIM__", "\0")
            .replace("__REFRESH__", str(REFRESH_S))
            .replace("__STEP__", html.escape(laps.now or "starting"))
            .replace("__SECS__", str(int(laps.elapsed())))
            .replace("\0", ANIM_JS))


# The field growing (#1115): the real shape from /__boot/shape (or a generated ball when there is
# none), points appearing as their rows load, lines between them, pulses along the lines once
# recall's models are up; when /global/health stops being the door's 503 the field brightens and
# the page reloads into the UI. No external assets; the door answers without auth, so nothing of
# the field's text is ever shown. (Speeds and sizes below are cosmetic.)
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>membrane: starting</title>
<style>:root{--bg:#f6f7f9;--fg:#2a2f36;--dot:40,70,140;--line:40,70,140;--pulse:220,120,30}
@media (prefers-color-scheme:dark){:root{--bg:#0b0d12;--fg:#c9d1dc;--dot:150,190,255;--line:120,160,230;--pulse:255,190,90}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);overflow:hidden;font:14px system-ui,sans-serif}
canvas{position:fixed;inset:0;width:100%;height:100%}
#s{position:fixed;left:0;right:0;bottom:24px;text-align:center;opacity:.65;padding:0 16px}</style></head>
<body><canvas id="f"></canvas><div id="s">__STEP__ &middot; __SECS__s</div>
<noscript><meta http-equiv="refresh" content="__REFRESH__"><p style="position:fixed;top:40%;width:100%;text-align:center">loading the field&hellip;</p></noscript>
<script>__ANIM__</script>
<script>
(function(){
var R=__REFRESH__*1000,cv=document.getElementById('f'),txt=document.getElementById('s');
var cs=getComputedStyle(document.documentElement),C=function(n){return cs.getPropertyValue(n).trim();};
var a=fieldAnim(cv,{colors:{dot:C('--dot'),line:C('--line'),pulse:C('--pulse'),bg:C('--bg')}});
a.start();
fetch('/__boot/shape',{headers:{Accept:'application/json'},cache:'no-store'}).then(function(r){return r.json();})
.then(function(d){a.shape(d);}).catch(function(){a.shape(null);});
var w=new SeatWatch({fails:2});
function poll(){SeatWatch.probe(fetch.bind(window)).then(function(e){var r=w.note(e);
if(e.kind==='loading'){a.set({dir:1,colour:'coloured',progress:r.progress,recall:!!e.d.recall});txt.textContent=(e.d.step||'starting')+' \u00b7 '+Math.round(e.d.elapsed_s||0)+'s';setTimeout(poll,R);}
else if(e.kind==='up'){a.set({ready:true,dir:1,colour:'coloured'});txt.textContent='ready';setTimeout(function(){location.reload();},1400);}
else{if(r.mode==='offline'){a.set({dir:-1,colour:'grey'});txt.textContent='the seat is not reachable \u00b7 waiting for it';}setTimeout(poll,R);}});}
poll();
})();
</script></body></html>"""


class BootDoor:
    """The serve port's answer while the seat boots (#1115). The boot reserves the serve port at its
    top (bound + listening) and the native serve adopts that socket once everything is up; in between
    the door answers on it: 503 + Retry-After for every request, with the step the boot is on
    (`laps.now`) -- the field-growing page for a browser (page_html), the status JSON for the rest
    (status()), and the last clean stop's field shape at /__boot/shape (numbers only). Never 200: nothing
    reads a half-booted seat as ready (Home Assistant's readiness is /global/health == 200).
    `handover()` stops answering and leaves the socket open for the real server (connections that
    arrive in between wait in its backlog)."""

    def __init__(self, sock, laps: Laps, shape: bytes | None = None):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        door = self

        class _H(BaseHTTPRequestHandler):
            def _loading(self):
                import json
                if self.path.split("?", 1)[0] == "/__boot/shape":
                    body = door.shape or json.dumps({"shape": None}).encode()
                    ctype = "application/json"
                elif "text/html" in (self.headers.get("Accept") or ""):
                    body = page_html(door.laps).encode()
                    ctype = "text/html; charset=utf-8"
                else:
                    body = json.dumps(status(door.laps, door.shape)).encode()
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

        self.laps, self.shape = laps, shape
        self.httpd = ThreadingHTTPServer(sock.getsockname()[:2], _H, bind_and_activate=False)
        self.httpd.socket = sock          # the reservation: answered here, never rebound
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda request, client_address: None   # a tab that hung up: nothing to log
        self._th = threading.Thread(target=self.httpd.serve_forever, name="boot-door", daemon=True)
        self._th.start()

    def handover(self) -> None:
        """Stop answering; the socket stays open for the native serve to adopt. Idempotent."""
        if self._th is None:
            return
        self.httpd.shutdown()
        self._th = None
