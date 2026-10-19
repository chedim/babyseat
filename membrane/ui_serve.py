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

THE LAN GUARD: standalone's pages answer a private, loopback or tailnet
address (a person types their name in the page). Anything forwarded by a proxy
(X-Forwarded-For, Forwarded) or from a public address gets a 403, never a login
prompt -- unless it carries the seat token. THE OWNER'S DOOR (#253,
seatauth.py) sits in front of every write: from another machine the page reads
freely and asks once for the seat token (POST /ui/login sets the same HttpOnly
cookie JWT mode uses); localhost needs nothing. /ui/conf says `auth: "seat"`
and whether this caller is the `owner`.

JWT MODE (jwtauth.py, docs/auth.md; off unless MEMBRANE_JWT_SECRET[_FILE] is
set): the token replaces the LAN guard. The page's shell (/, /index.html,
/ui/www/*, the favicon) is public so the token prompt can render; everything
else needs a token (Bearer, Basic with the JWT as password, or the HttpOnly
`membrane_jwt` cookie POST /ui/login sets) -- from the LAN or behind an
ingress alike. The speaker line is the token's, never the page's name.

THE SPEAKER LINE (open mode): the page sends the name it holds (X-Membrane-Speaker); every
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
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from . import jwtauth as _JWT
from . import seatauth as _SA
from . import settings as S
from .speakers import STANDALONE_PREFIX, id_line, strip_speaker_lines

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
MAX_STT = 15_400_000                       # 8 min of 16 kHz s16 mono + header (under HA's 16 MiB too)
STT_TIMEOUT_S = 180                        # long audio takes the speech server a while
TAILNET = ipaddress.ip_network("100.64.0.0/10")
SHELL = ("/", "/membrane", "/index.html", "/favicon.ico")
MAX_LOGIN = 16_384


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


def token_person(handler):
    """The token's identity in JWT mode (None: open mode, or the seat's own
    internal token -- those keep whatever line they carry)."""
    ident = getattr(handler, "_identity", None)
    return ident if ident is not None and not ident.internal else None


def with_identity(body: dict, ident) -> dict:
    """JWT mode: the message body with the TOKEN's line as its first text
    part; every speaker line a client put in any text part is cut."""
    parts = []
    for p in body.get("parts") or []:
        if isinstance(p, dict) and p.get("type") == "text":
            t = strip_speaker_lines(str(p.get("text") or ""))
            if not t.strip():
                continue
            p = dict(p, text=t)
        parts.append(p)
    out = dict(body)
    out["parts"] = [{"type": "text", "text": id_line(ident.sub, ident.name), "synthetic": True}] + parts
    return out


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
    ident = token_person(handler)
    if ident is not None:
        body = {**body, "person": ident.label} if voice else with_identity(body, ident)
    else:
        name = speaker_of(handler)
        body = {**body, "person": name} if voice else with_speaker(body, name)
    _replace_body(handler, json.dumps(body).encode())
    return True


def stamp(handler) -> bool:
    """JWT mode, a direct (non-page) request: a message / voice body gets the
    token's identity. False = bad JSON. Open mode / the seat's own: untouched."""
    if token_person(handler) is None:
        return True
    return _restamp(handler, handler.path.partition("?")[0])


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


def ui_build_id(root: Path = UI_DIR) -> str:
    """#243: the page code's fingerprint -- a hash over every file under the
    UI folder (name + bytes). A restart into new code changes it, and the
    open tab (reused now, not reopened) reloads itself when it sees that."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode() + b"\0")
            h.update(p.read_bytes() + b"\0")
    return h.hexdigest()[:16]


_BUILD: list[str] = []
_BUILD_LOCK = threading.Lock()


def build_id() -> str:
    """The build this boot serves (computed once: a re-exec is a new boot)."""
    with _BUILD_LOCK:
        if not _BUILD:
            try:
                _BUILD.append(ui_build_id(UI_DIR))
            except OSError:
                _BUILD.append("")
        return _BUILD[0]


def conf() -> dict:
    from . import naming as NM
    st = S.load()
    name = NM.current()
    return {"serve_ok": True, "serve_url": "standalone", "standalone": True, "build": build_id(),
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
        return _json(handler, {"error": "too long (8 minutes at most)"}, 413)
    wav = handler.rfile.read(n) if n else b""
    if not (len(wav) > 44 and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"):
        return _json(handler, {"error": "expected a WAV recording"}, 400)
    from . import naming as NM
    name = NM.current()
    body, ctype = _multipart(wav, ", ".join([name] + list(st.get("aliases") or [])) + ".")
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
        with urllib.request.urlopen(req, timeout=STT_TIMEOUT_S) as up:
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

def public_path(handler) -> bool:
    """JWT mode's open door: the page's static shell (so the token prompt can
    render) and the login/logout pair. Standalone only."""
    if not enabled():
        return False
    path = handler.path.partition("?")[0]
    if handler.command in ("GET", "HEAD"):
        return path in SHELL or path.startswith("/ui/www/")
    return handler.command == "POST" and path in ("/ui/login", "/ui/logout")


def _login(handler) -> None:
    """POST /ui/login {"token": "<jwt>"}: a valid person's token becomes the
    HttpOnly, SameSite=Strict cookie (Secure over https). JSON only -- a
    cross-site form can't post it; the seat's internal token never logs in.
    JWT mode off (#253): the SEAT token (seatauth.py) is the one accepted --
    the cookie then opens the off-box writes."""
    ctype = str(handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    n = int(handler.headers.get("Content-Length") or 0)
    if ctype != "application/json":
        handler.close_connection = True
        return _json(handler, {"error": "send JSON: {\"token\": \"...\"}"}, 415)
    if n > MAX_LOGIN:
        handler.close_connection = True
        return _json(handler, {"error": "too large"}, 413)
    tok = None
    if not _JWT.enabled():
        try:
            tok = json.loads(handler.rfile.read(n) if n else b"{}").get("token")
        except (ValueError, AttributeError):
            tok = None
        if not _SA.matches(tok):
            return _JWT.send_unauthorized(handler)
        b = json.dumps({"ok": True, "who": None}).encode()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(b)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Set-Cookie", _SA.login_cookie(tok, _JWT.request_is_https(handler.headers)))
        handler.end_headers()
        handler.wfile.write(b)
        return
    try:
        tok = json.loads(handler.rfile.read(n) if n else b"{}").get("token")
        ident = _JWT.verify_configured(tok)
    except (ValueError, AttributeError, _JWT.JWTConfigError):
        ident = None
    if ident is None or ident.internal or not isinstance(tok, str):
        return _JWT.send_unauthorized(handler)
    b = json.dumps({"ok": True, "who": {"sub": ident.sub, "name": ident.name}}).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(b)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Set-Cookie", _JWT.login_cookie(tok, ident, _JWT.request_is_https(handler.headers)))
    handler.end_headers()
    handler.wfile.write(b)


def _logout(handler) -> None:
    n = int(handler.headers.get("Content-Length") or 0)
    if n > MAX_LOGIN:
        handler.close_connection = True
    elif n:
        handler.rfile.read(n)
    b = b'{"ok": true}'
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(b)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Set-Cookie", _JWT.logout_cookie(_JWT.request_is_https(handler.headers)))
    handler.end_headers()
    handler.wfile.write(b)


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
    jwt = _JWT.enabled()
    # JWT mode: the token (checked by the serve's gate, jwtauth.gate) is the
    # door, not the address -- it works behind an ingress
    # ... off (#253): the LAN guard, or the seat token (a proxied page that
    # logged in with it gets in too)
    lan = getattr(handler, "_identity", None) is not None if jwt \
        else (trusted(handler) or _SA.presented_seat_token(handler, cookie=True, basic=True))
    if lan:
        handler._ui_trusted = True         # standalone: no password anywhere on the LAN (the operator's rule)
    # /membrane too: the page rewrote the bar to the HA panel's url_path
    # (panel.js's landing hop, meaningless here -- HA serves that URL, the
    # seat doesn't), and a reload of it used to die with a bare not-found
    # (the operator's exhibit, 2026-10-13). Serve the page; both spellings.
    if not (path in ("/", "/membrane", "/index.html") or path == "/favicon.ico"
            or path.startswith("/ui/")):
        return False
    if handler.command == "POST" and path in ("/ui/login", "/ui/logout"):
        _login(handler) if path == "/ui/login" else _logout(handler)
        return True
    if jwt and not lan and not public_path(handler):
        _JWT.send_unauthorized(handler)        # the serve's gate answered first; a belt to its braces
        return True
    if not jwt and not lan:
        _json(handler, {"error": "membrane answers the local network only"}, 403)   # never a login prompt
        return True
    if path.startswith("/ui/serve/"):
        rest = "/" + path[len("/ui/serve/"):].lstrip("/")
        if not _restamp(handler, rest):
            _json(handler, {"type": "error", "error": {"message": "body must be a JSON object"}}, 400)
            return True
        handler.path = rest + ("?" + qs if qs else "")
        handler._ui_trusted = True         # lan, or the token checked above
        handler._ui_relay = True           # the page's request: its person's sessions (server.py)
        _who = token_person(handler)
        handler._ui_speaker = _who.sub if _who is not None else speaker_of(handler)
        return False
    if handler.command not in ("GET", "HEAD") and path not in ("/ui/stt", "/ui/log"):
        _json(handler, {"error": "method not allowed"}, 405)
        return True
    if path in ("/", "/membrane", "/index.html"):
        _send_file(handler, UI_DIR / "index.html")
    elif path == "/favicon.ico":
        _send_file(handler, UI_DIR / "favicon.svg")
    elif path == "/ui/conf":
        c = conf()
        if jwt:                            # JWT mode: who the token says you are (the page asks no name)
            _id = getattr(handler, "_identity", None)
            c["auth"] = "jwt"
            c["who"] = {"sub": _id.sub, "name": _id.name} if _id is not None else None
        else:                              # #253: off-box writes need the seat token
            c["auth"] = "seat"
            c["owner"] = _SA.owner(handler, cookie=True, basic=True)
        _json(handler, c)
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


# #243 ONE TAB: a membrane page already open reconnects by itself after a
# boot (its /event stream retries, then resyncs) -- the boot waits a short
# window for that before opening a new tab. Set by the serve's SSE route
# (server.py _sse) when a stream client connects; the seat's own hub
# listeners subscribe directly and never count.
_PAGE_SEEN = threading.Event()
GRACE_S = 8.0


def note_page() -> None:
    """A page's live stream connected (an open tab is here)."""
    _PAGE_SEEN.set()


def open_when_up(port: int, url: str, timeout_s: float = 60.0,
                 grace_s: float = GRACE_S, reexec: bool = False):
    """Open the UI in the default browser once the port answers (a thread),
    unless a page reconnects within `grace_s` of the port coming up (#243).
    Never on a re-exec restart (`reexec`, restarts.was_reexec) or with
    MEMBRANE_NO_BROWSER=1. Returns the thread (None when nothing will open)."""
    import socket
    import time
    import webbrowser

    if reexec or os.environ.get("MEMBRANE_NO_BROWSER") == "1":
        return None

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
        if _PAGE_SEEN.wait(grace_s):
            return                         # an open tab came back: no new one
        try:
            webbrowser.open(url)
        except Exception:
            pass

    t = threading.Thread(target=_go, name="ui-browser", daemon=True)
    t.start()
    return t
