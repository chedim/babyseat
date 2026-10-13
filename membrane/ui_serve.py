"""STANDALONE MODE (2026-10-12, the operator: "lets ship it fully ha-independent
and standalone? like, keep the ui, but HA should not be required"): the seat
serves the web UI built for HA -- the chat, membrane home, the 3D field, voice
-- itself, on its serve port, with no Home Assistant anywhere.

    /                 the page (ui/index.html: the name prompt, then membrane home)
    /ui/www/<file>    the UI's files (ui/ in the package; the big models from
                      <data dir>/assets/, or the HA integration's www/ in a checkout)
    /ui/conf          the page's config (the seat's name, voice)
    /ui/stt           speech to text: the settings' whisper-server, or 501 (the
                      page then listens in the browser)
    /ui/serve/<rest>  the seat's own routes (/session, /event, /membrane/...),
                      what HA's serve_proxy relayed -- in process

THE LAN GUARD: standalone asks no password of a private, loopback or tailnet
address -- on any route, the UI's or the seat's own (the operator: "no auth
needed"; a person types their name in the page). Anything forwarded by a proxy
(X-Forwarded-For, Forwarded) or from a public address gets a 403, never a login
prompt (the basic-auth pair still opens it for scripts).

THE SPEAKER LINE: the page sends the name it holds (X-Membrane-Speaker); every
message posted through /ui/serve carries '[user: <name>]' as its first line
(speakers.split_speaker), a page-sent line replaced, and the voice / JS routes
get it as `person` -- serve_proxy's rule, without HA.
"""
from __future__ import annotations

import io
import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import settings as S
from .speakers import STANDALONE_PREFIX

UI_DIR = Path(__file__).resolve().parent / "ui"
# a checkout's HA integration: the big models live there (git-ignored, not packaged)
DEV_WWW = (Path(__file__).resolve().parent.parent / "integrations" / "homeassistant"
           / "custom_components" / "ai_terminal" / "www")
TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
         ".mjs": "application/javascript; charset=utf-8", ".wasm": "application/wasm",
         ".gz": "application/gzip", ".json": "application/json", ".css": "text/css; charset=utf-8",
         ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
BIG = (".wasm", ".gz")                     # long cache: they change with the release
VOICE_PATHS = ("/membrane/voice", "/membrane/voice/state", "/membrane/jsexec/register", "/membrane/jsexec/result")
_MSG = re.compile(r"^/session/[^/]+/(message|prompt_async)$")
_ANY_SPEAKER = re.compile(r"^\s*\[(?:HA )?user: ")
_NAME_OK = re.compile(r"^[^\x00-\x1f\[\]]{1,60}$")
MAX_STT = 2_200_000                        # 60 s of 16 kHz s16 mono + header
TAILNET = ipaddress.ip_network("100.64.0.0/10")


# -- the LAN guard ------------------------------------------------------------

def lan_client(handler) -> bool:
    if handler.headers.get("X-Forwarded-For") or handler.headers.get("Forwarded"):
        return False                       # a reverse proxy: its clients may be anyone
    try:
        ip = ipaddress.ip_address(str(handler.client_address[0]).split("%")[0])
    except (ValueError, IndexError, TypeError):
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or (ip.version == 4 and ip in TAILNET)


def trusted(handler) -> bool:
    return bool(S.load().get("ui_lan_open", True)) and lan_client(handler)


# -- the speaker --------------------------------------------------------------

def speaker_of(handler) -> str:
    raw = urllib.parse.unquote(handler.headers.get("X-Membrane-Speaker") or "")
    name = " ".join(raw.split())
    return name if _NAME_OK.match(name or "") else "guest"


def with_speaker(body: dict, name: str) -> dict:
    """The message body with '[user: <name>]' as its FIRST text part; a
    speaker line the page sent itself (either form) is dropped."""
    parts = [p for p in (body.get("parts") or [])
             if not (isinstance(p, dict) and p.get("type") == "text"
                     and _ANY_SPEAKER.match(str(p.get("text") or "")))]
    out = dict(body)
    out["parts"] = [{"type": "text", "text": f"{STANDALONE_PREFIX}{name.replace(']', ')')}]",
                     "synthetic": True}] + parts
    return out


def _replace_body(handler, data: bytes) -> None:
    handler.rfile = io.BytesIO(data)
    del handler.headers["Content-Length"]
    handler.headers["Content-Length"] = str(len(data))


def _restamp(handler, path: str) -> bool:
    """Rewrite the request body for a message / voice route. False = bad JSON."""
    if handler.command not in ("POST", "PATCH"):
        return True
    voice = path in VOICE_PATHS
    if not (voice or _MSG.match(path)):
        return True
    n = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(n) if n else b""
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    name = speaker_of(handler)
    body = {**body, "person": name} if voice else with_speaker(body, name)
    _replace_body(handler, json.dumps(body).encode())
    return True


# -- the files ----------------------------------------------------------------

def find_file(name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    for d in (UI_DIR, S.data_dir() / "assets", DEV_WWW):
        p = d / name
        if p.is_file():
            return p
    from . import assets                     # a big voice file: fetched on first use, verified
    return assets.ensure(name, S.data_dir() / "assets")


def _send_file(handler, p: Path) -> None:
    size = p.stat().st_size
    handler.send_response(200)
    handler.send_header("Content-Type", TYPES.get(p.suffix, "application/octet-stream"))
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "public, max-age=604800" if p.suffix in BIG else "no-cache")
    handler.end_headers()
    if handler.command == "HEAD":
        return
    with p.open("rb") as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            handler.wfile.write(chunk)


def _json(handler, obj, code: int = 200) -> None:
    b = json.dumps(obj).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(b)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(b)


def conf() -> dict:
    from . import naming as NM
    st = S.load()
    name = NM.current()
    return {"serve_ok": True, "serve_url": "standalone", "standalone": True,
            "name": name, "named": bool(st.get("name")), "aliases": list(st.get("aliases") or []),
            "stt": "server" if str(st.get("stt") or "").startswith("http") else "browser",
            "voice_out": bool(st.get("voice_out")), "language": st.get("language") or "en"}


# -- speech to text -----------------------------------------------------------

def _multipart(wav: bytes, hint: str) -> tuple[bytes, str]:
    b = "membrane" + uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"u.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n").encode() + wav + \
           (f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson"
            f"\r\n--{b}\r\nContent-Disposition: form-data; name=\"prompt\"\r\n\r\n{hint}"
            f"\r\n--{b}--\r\n").encode()
    return body, f"multipart/form-data; boundary={b}"


def _stt(handler) -> None:
    st = S.load()
    url = str(st.get("stt") or "")
    if not url.startswith("http"):
        return _json(handler, {"error": "no speech server: the page listens itself"}, 501)
    n = int(handler.headers.get("Content-Length") or 0)
    if n > MAX_STT:
        return _json(handler, {"error": "too long (60 s at most)"}, 413)
    wav = handler.rfile.read(n) if n else b""
    if not (len(wav) > 44 and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"):
        return _json(handler, {"error": "expected a WAV recording"}, 400)
    from . import naming as NM
    name = NM.current()
    body, ctype = _multipart(wav, ", ".join([name] + list(st.get("aliases") or [])) + ".")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
        with urllib.request.urlopen(req, timeout=45) as up:
            text = json.loads(up.read() or b"{}").get("text", "")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _json(handler, {"error": f"the speech server is not answering ({type(e).__name__})"}, 502)
    text = " ".join(re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", str(text or "")).split())
    return _json(handler, {"text": text})


# -- the page's voice log -----------------------------------------------------

PAGE_LOG = "page-voice.log"
PAGE_LOG_MAX = 2_000_000


def _page_log(handler) -> None:
    """One line of the page's voice log (home.js speechLog), appended for the
    engineer to read: what the page heard, how loud, which engine, what failed."""
    n = min(int(handler.headers.get("Content-Length") or 0), 4096)
    raw = handler.rfile.read(n) if n else b""
    try:
        line = json.dumps(json.loads(raw), ensure_ascii=False)
        p = S.data_dir() / PAGE_LOG
        if p.exists() and p.stat().st_size > PAGE_LOG_MAX:
            os.replace(p, p.with_suffix(".log.old"))
        with p.open("a") as f:
            f.write(line + "\n")
    except (ValueError, OSError):
        pass
    handler.send_response(204)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


# -- the hook -----------------------------------------------------------------

def enabled() -> bool:
    """Only a seat launched as standalone (`membrane standalone`, --standalone)
    serves the UI; a plain `membrane serve` (the boat's phone) never does."""
    return os.environ.get("MEMBRANE_STANDALONE") == "1"


def intercept(handler) -> bool:
    """Called first by every do_* of the serve's handler. True = answered here;
    False = carry on (a /ui/serve request comes back rewritten to the seat's
    own route, with handler._ui_trusted set when the LAN guard let it in)."""
    handler._ui_trusted = False            # keep-alive: one handler, many requests
    handler._ui_relay = False
    if not enabled():
        return False
    path, _, qs = handler.path.partition("?")
    lan = trusted(handler)
    if lan:
        handler._ui_trusted = True         # standalone: no password anywhere on the LAN (the operator's rule)
    # /membrane too: the page rewrote the bar to the HA panel's url_path
    # (panel.js's landing hop, meaningless here -- HA serves that URL, the
    # seat doesn't), and a reload of it used to die with a bare not-found
    # (the operator's exhibit, 2026-10-13). Serve the page; both spellings.
    if not (path in ("/", "/membrane", "/index.html") or path == "/favicon.ico"
            or path.startswith("/ui/")):
        return False
    if not lan and not handler._authed():
        _json(handler, {"error": "membrane answers the local network only"}, 403)   # never a login prompt
        return True
    if path.startswith("/ui/serve/"):
        rest = "/" + path[len("/ui/serve/"):].lstrip("/")
        if not _restamp(handler, rest):
            _json(handler, {"type": "error", "error": {"message": "body must be a JSON object"}}, 400)
            return True
        handler.path = rest + ("?" + qs if qs else "")
        handler._ui_trusted = True         # lan, or the pair checked above
        handler._ui_relay = True           # the page's request: its person's sessions (server.py)
        handler._ui_speaker = speaker_of(handler)
        return False
    if handler.command not in ("GET", "HEAD") and path not in ("/ui/stt", "/ui/log"):
        _json(handler, {"error": "method not allowed"}, 405)
        return True
    if path in ("/", "/membrane", "/index.html"):
        _send_file(handler, UI_DIR / "index.html")
    elif path == "/favicon.ico":
        _send_file(handler, UI_DIR / "favicon.svg")
    elif path == "/ui/conf":
        _json(handler, conf())
    elif path == "/ui/log":
        _page_log(handler)
    elif path == "/ui/stt":
        if handler.command != "POST":
            _json(handler, {"error": "POST a WAV"}, 405)
        else:
            _stt(handler)
    elif path.startswith("/ui/www/"):
        p = find_file(urllib.parse.unquote(path[len("/ui/www/"):]))
        if p is None:
            _json(handler, {"error": "not found"}, 404)
        else:
            _send_file(handler, p)
    else:
        _json(handler, {"error": "not found"}, 404)
    return True


def browser_url(host: str, port: int) -> str:
    h = "localhost" if host in ("0.0.0.0", "::", "", "127.0.0.1") else host
    return f"http://{h}:{port}/"


def open_when_up(port: int, url: str, timeout_s: float = 60.0) -> None:
    """Open the UI in the default browser once the port answers (a thread)."""
    import socket
    import threading
    import time
    import webbrowser

    def _go():
        t0 = time.time()
        while time.time() - t0 < timeout_s:
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.3)
        else:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if os.environ.get("MEMBRANE_NO_BROWSER") != "1":
        threading.Thread(target=_go, name="ui-browser", daemon=True).start()
