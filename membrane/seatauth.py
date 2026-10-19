"""THE OWNER'S DOOR (issue #253): off-box writes need a credential.

Both ports bind every interface (the phone and Home Assistant reach the seat
over Wi-Fi: opencode.proxy_bind_host). Reads stay open on the LAN; localhost
is the owner's console and needs nothing. From any OTHER address, every
state-changing request -- a write to the field, an entity, a method, a script,
a model, the toybox, the settings, a session, a turn on /v1 or Ollama's
/api/chat|generate, the seat's controls -- needs a token:

  - JWT mode on (jwtauth.py): a valid JWT, exactly as before (that gate
    already asks it of every request; nothing here changes it);
  - JWT mode off: the SEAT TOKEN, a random secret made on first boot at
    ~/.membrane/seat-token (0600; MEMBRANE_SEAT_TOKEN_FILE moves it). It
    rides `Authorization: Bearer <token>`, on :4096 also HTTP Basic with
    the token as the password (Home Assistant, opencode attach) or the
    HttpOnly cookie the UI's login sets. `membrane token seat` prints it.

THE OWNER IS A CREDENTIAL, never a speaker line: loopback is decided by the
socket's peer address alone. A forwarding header (X-Forwarded-For, Forwarded,
X-Real-IP) only ever makes a caller count as off-box: behind a proxy, every
caller is someone else until it shows the token.

Script-bearing fields are code: an off-box caller without the token never
lands one, whatever the settings say (the route gate, and a second check
where the scripts are written).
"""
from __future__ import annotations

import hmac
import ipaddress
import os
import secrets
import stat
import sys
import threading
from pathlib import Path

from . import jwtauth as _JWT

FILE_ENV = "MEMBRANE_SEAT_TOKEN_FILE"
PRINT_ENV = "MEMBRANE_SEAT_TOKEN_PRINT"       # the container's entrypoint sets it: print it at boot
COOKIE_AGE = 400 * 86400                      # the browsers' own cap on a cookie's life
FORWARD_HEADERS = ("X-Forwarded-For", "Forwarded", "X-Real-IP")

# :11500's POSTs that only read (a query, a listing, an inspection): open on
# the LAN like the GET reads. Everything else that is not GET/HEAD is a write.
PROXY_READ_POSTS = frozenset({
    "/membrane/entity-query",
    "/membrane/entity-track/list",
    "/membrane/method/inspect",
    "/membrane/codegraph",
})
# :11500's GETs that change state (an intent is created and announced)
PROXY_WRITE_GETS = frozenset({"/membrane/intend"})
# :4096's non-GET routes that must stay reachable: the login pair
SERVE_OPEN_POSTS = frozenset({"/ui/login", "/ui/logout"})


# -- the token file -------------------------------------------------------------

def token_file() -> Path:
    """Where the seat token lives: MEMBRANE_SEAT_TOKEN_FILE, else
    ~/.membrane/seat-token (the owner's home: the same file whatever
    directory the seat or the CLI runs in; in the container HOME is /data)."""
    p = os.environ.get(FILE_ENV, "").strip()
    return Path(p).expanduser() if p else Path.home() / ".membrane" / "seat-token"


_lock = threading.Lock()
_cache: dict = {}


def _read(path: Path) -> str | None:
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if _cache.get("key") != key:
        try:
            tok = path.read_text().strip()
        except OSError:
            return None
        _cache.clear()
        _cache.update(key=key, token=tok or None)
    return _cache.get("token")


def ensure(rotate: bool = False) -> str:
    """The seat token, made on first use (0600, never group/world readable;
    an existing file with looser bits is tightened). `rotate` replaces it."""
    path = token_file()
    with _lock:
        if not rotate:
            tok = _read(path)
            if tok:
                try:
                    if stat.S_IMODE(path.stat().st_mode) & 0o077:
                        os.chmod(path, 0o600)
                except OSError:
                    pass
                return tok
        path.parent.mkdir(parents=True, exist_ok=True)
        tok = secrets.token_urlsafe(32)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.fchmod(fd, 0o600)                      # whatever the umask said
            os.write(fd, (tok + "\n").encode())
        finally:
            os.close(fd)
        os.replace(tmp, path)
        _cache.clear()
        return tok


def seat_token() -> str | None:
    """The current seat token, or None (no file and none can be made: then no
    presented token matches -- off-box writes fail closed)."""
    tok = _read(token_file())
    if tok:
        return tok
    try:
        return ensure()
    except OSError:
        return None


def matches(presented) -> bool:
    want = seat_token()
    return bool(want and isinstance(presented, str) and presented
                and hmac.compare_digest(presented.encode(), want.encode()))


# -- who is asking ----------------------------------------------------------------

def is_loopback(handler) -> bool:
    """The socket's peer is this machine. Never a header: a forwarding header
    only ever makes the caller count as off-box."""
    for h in FORWARD_HEADERS:
        if handler.headers.get(h) is not None:
            return False
    try:
        ip = ipaddress.ip_address(str(handler.client_address[0]).split("%")[0])
    except (ValueError, IndexError, TypeError, AttributeError):
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback


def presented_seat_token(handler, cookie: bool = False, basic: bool = False) -> bool:
    """JWT mode off: did the request carry the seat token?"""
    return matches(_JWT.token_of(handler.headers, cookie=cookie, basic=basic))


def owner(handler, cookie: bool = False, basic: bool = False) -> bool:
    """Is this request the seat's owner? JWT mode: it carried a valid token
    (jwtauth.gate set handler._identity). Off: loopback, or the seat token.
    An in-process call (no socket at all) is the seat itself."""
    if _JWT.enabled():
        return getattr(handler, "_identity", None) is not None
    if not hasattr(handler, "client_address"):
        return True
    return is_loopback(handler) or presented_seat_token(handler, cookie=cookie, basic=basic)


# -- which requests are writes ------------------------------------------------------

def proxy_is_write(method: str, path: str) -> bool:
    """:11500 -- every non-GET/HEAD but the read-only POSTs, plus the GETs
    that change state. /v1/chat/completions and /api/chat|generate are
    writes: every turn is captured into the field, its frames learn/forget/
    watch/intend, and its tool rounds reach the entity and script lanes."""
    if method in ("GET", "HEAD"):
        return path in PROXY_WRITE_GETS
    return path not in PROXY_READ_POSTS


def serve_is_write(method: str, path: str) -> bool:
    """:4096 -- every non-GET/HEAD (sessions, prompts, models, the toybox,
    the seat's controls, voice, HA's token, /tui/*) but the UI's login pair."""
    if method in ("GET", "HEAD"):
        return False
    return path not in SERVE_OPEN_POSTS


def gate(handler, write: bool, cookie: bool = False, basic: bool = False) -> bool:
    """The owner's door, after jwtauth.gate: True = carry on; False = 401
    answered here. JWT mode: that gate is the door (nothing more here)."""
    if not write or _JWT.enabled():
        return True
    if owner(handler, cookie=cookie, basic=basic):
        return True
    _JWT.send_unauthorized(handler)
    return False


def carries_code(body) -> bool:
    """A body that writes a script (any *_script field with a value)."""
    return isinstance(body, dict) and any(
        str(k).endswith("_script") and v not in (None, "") for k, v in body.items())


# -- the UI's cookie --------------------------------------------------------------

def login_cookie(token: str, secure: bool) -> str:
    return (f"{_JWT.COOKIE}={token}; Path=/; Max-Age={COOKIE_AGE}; HttpOnly; SameSite=Strict"
            + ("; Secure" if secure else ""))


# -- boot + the CLI -----------------------------------------------------------------

def boot() -> None:
    """At boot (JWT mode off): make the token if it is missing and say where
    it is. MEMBRANE_SEAT_TOKEN_PRINT=1 (the container) prints the token."""
    if _JWT.enabled():
        return
    try:
        tok = ensure()
    except OSError as e:
        print(f"[auth] the seat token could not be written at {token_file()} ({e.strerror}): "
              "off-box writes are refused until it can", file=sys.stderr)
        return
    print(f"[auth] off-box writes need the seat token ({token_file()}; "
          "`membrane token seat` prints it); localhost needs none", file=sys.stderr)
    if os.environ.get(PRINT_ENV) == "1":
        print(f"[auth] seat token: {tok}", file=sys.stderr)


def cli_seat(rotate: bool = False, show_path: bool = False) -> int:
    if show_path:
        print(token_file())
        return 0
    try:
        tok = ensure(rotate=rotate)
    except OSError as e:
        print(f"membrane token: cannot write {token_file()} ({e.strerror})", file=sys.stderr)
        return 2
    print(tok)
    if rotate:
        print("membrane token: rotated -- give the new token to every off-box client "
              "(the old one stops working now)", file=sys.stderr)
    return 0
