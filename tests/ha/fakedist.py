"""A fake release for the seat runner's tests (#1143): a dist folder whose bin/cliffvm is a tiny Python script (never the real seat), packed the way
scripts/dist/build.sh --archive packs one (one top folder, MANIFEST.sha256, a .sha256 sidecar), and a release server on 127.0.0.1 that serves the files
of a folder and counts what it was asked.

The fake `cliffvm serve` reads <data dir>/fake.json (what the test wants it to do) and appends one line per start to <data dir>/fake-runs.jsonl (its argv,
its environment and its pid):
  {"mode": "ok"}                       writes admin.token, answers GET /global/health with that token (Basic or Bearer), 401 without; SIGTERM exits 0
  {"mode": "ok", "exit_after": S, "code": C}   the same, then exits C after S seconds
  {"mode": "exit", "code": C}          exits C at once, after a stderr line that holds a token-shaped string
  {"mode": "nohealth"}                 writes the token and never answers health (no listener)
  {"mode": "ignore_term"}              like ok, but SIGTERM is ignored (the runner has to kill it)
  GET/POST /membrane/options           the seat's model pick: `want["loops"]` (default "first-model") until a POST {loops} writes <data>/fake-loops; each POST is a line of fake-posts.jsonl
`cliffvm token mint ...` prints `minted-<n>` (n counts the mints in <secret file>.mints).
"""
from __future__ import annotations

import hashlib
import sys
import tarfile
from pathlib import Path

from aiohttp import web

FAKE_CLIFFVM = r'''#!__PY__
import base64, json, os, signal, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
a = sys.argv[1:]
def opt(name, default=None):
    return a[a.index(name) + 1] if name in a else default
if a[:2] == ["token", "mint"]:
    f = opt("--secret-file") + ".mints"
    n = int(open(f).read()) + 1 if os.path.exists(f) else 1
    open(f, "w").write(str(n))
    print(f"minted-{n}")
    sys.exit(0)
assert a[0] == "serve", a
data = opt("--data-dir")
port = int(opt("--port"))
bind = opt("--bind")
try:
    want = json.load(open(os.path.join(data, "fake.json")))
except OSError:
    want = {"mode": "ok"}
with open(os.path.join(data, "fake-runs.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv, "env": dict(os.environ), "pid": os.getpid(), "cwd": os.getcwd()}) + "\n")
mode = want.get("mode", "ok")
print("cliffvm serve: data " + data, flush=True)
sys.stderr.write("cliffvm: a token-shaped string eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhZG1pbiJ9.c2lnbmF0dXJl and a key sk-ant-SECRET-abcdef\n"); sys.stderr.flush()
if mode == "exit":
    sys.stderr.write("cliffvm: the fake was told to fail\n"); sys.stderr.flush()
    sys.exit(int(want.get("code", 3)))
token = "tok-%d" % os.getpid()
open(os.path.join(data, "door.secret"), "w").write("secret")
tmp = os.path.join(data, ".admin.token.tmp")
open(tmp, "w").write(token + "\n")
os.replace(tmp, os.path.join(data, "admin.token"))
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
else:
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
if mode == "nohealth":
    while True:
        time.sleep(1)
class H(BaseHTTPRequestHandler):
    def log_message(self, *x):
        pass
    def do_GET(self):
        h = self.headers.get("Authorization", "")
        got = base64.b64decode(h[6:]).decode().partition(":")[2] if h.startswith("Basic ") else (h[7:] if h.startswith("Bearer ") else "")
        code, body = (200, b'{"healthy":true}') if self.path == "/global/health" and got == token else ((401, b'{}') if got != token else (404, b'{}'))
        if self.path == "/membrane/options" and got == token:          # the seat's model pick (Minds:seat.loops), kept in <data>/fake-loops; each POST logged
            f = os.path.join(data, "fake-loops")
            if self.command == "POST":
                loops = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("loops")
                open(f, "w").write(loops)
                open(os.path.join(data, "fake-posts.jsonl"), "a").write(json.dumps({"loops": loops}) + "\n")
            cur = open(f).read() if os.path.exists(f) else want.get("loops", "first-model")
            code, body = 200, json.dumps({"background": {"loops": cur, "models": [cur]}}).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    do_POST = do_GET
srv = ThreadingHTTPServer((bind, port), H)
print(f"cliffvm: door listening on {bind}:{port}", flush=True)
if want.get("exit_after") is not None:
    def later():
        time.sleep(float(want["exit_after"]))
        os._exit(int(want.get("code", 3)))
    threading.Thread(target=later, daemon=True).start()
srv.serve_forever()
'''


def fake_binary() -> str:
    return FAKE_CLIFFVM.replace("__PY__", sys.executable)


def manifest(d: Path) -> None:
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(d).as_posix()}"
             for f in sorted(p for p in d.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")]
    (d / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")


def make_dist(d: Path, version: str) -> Path:
    (d / "bin").mkdir(parents=True, exist_ok=True)
    (d / "packs").mkdir(exist_ok=True)
    (d / "bin" / "cliffvm").write_text(fake_binary())
    (d / "bin" / "cliffvm").chmod(0o755)
    (d / "packs" / "boot.c1f").write_text("PACK boot 1\n")
    (d / "VERSION").write_text(version + "\n")
    manifest(d)
    return d


def make_release(root: Path, version: str, target: str, *, tamper_inner=False, bad_sha=False, sidecar=True, wrong_version=None,
                 extra_member=None) -> Path:
    """-> the folder that holds babyseat-<version>-<target>.tar.gz (+ .sha256), a release download folder."""
    rel = root / "release"
    rel.mkdir(parents=True, exist_ok=True)
    d = make_dist(root / "stage" / version, wrong_version or version)
    if tamper_inner:
        (d / "packs" / "boot.c1f").write_text("tampered after the manifest\n")
    tb = rel / f"babyseat-{version}-{target}.tar.gz"
    with tarfile.open(tb, "w:gz") as t:
        t.add(d, arcname=version)
        if extra_member is not None:
            name, data = extra_member
            import io
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    if sidecar:
        h = "0" * 64 if bad_sha else hashlib.sha256(tb.read_bytes()).hexdigest()
        (rel / (tb.name + ".sha256")).write_text(f"{h}  {tb.name}\n")
    return rel


class ReleaseServer:
    """Serves the files of `folder` at http://127.0.0.1:<port>/<name>; `asked` is every path asked, in order."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.asked: list[str] = []

    async def _get(self, req):
        self.asked.append(req.path)
        f = self.folder / req.path.lstrip("/")
        if not f.is_file():
            return web.Response(status=404, text="no such asset")
        return web.Response(body=f.read_bytes())

    async def __aenter__(self):
        app = web.Application()
        app.router.add_get("/{name:.*}", self._get)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def __aexit__(self, *exc):
        await self._runner.cleanup()
