"""JWT MODE (issue #203): an optional gate on both of the seat's ports.

OFF BY DEFAULT. With neither MEMBRANE_JWT_SECRET nor MEMBRANE_JWT_SECRET_FILE
set, nothing here runs and the seat behaves exactly as before. With a secret:

  - every request on :11500 (the OpenAI proxy) and :4096 (the serve API + UI)
    needs a valid token, or it gets 401 + `WWW-Authenticate: Bearer`; only
    the standalone UI's static shell stays public (the login prompt has to
    render somewhere);
  - the token rides `Authorization: Bearer <jwt>`; on :4096 also the
    HttpOnly `membrane_jwt` cookie (`POST /ui/login`) and HTTP Basic with
    the JWT as the password (opencode attach and Home Assistant speak Basic);
    a query-string token is never read;
  - the token's identity is who said it: a client's own speaker line is
    stripped and '[user#<sub>: <name>]' stamped instead (speakers.py);
  - the seat's own loopback callers carry an internal token minted here
    (sub 'seat:internal'); there is no bare-loopback exemption.

Tokens: HS256 only, a shared secret (>= 32 bytes), verified with the stdlib.
`exp` is required, `nbf`/`iat` are checked with a small leeway, `sub` is
required, `iss`/`aud` are checked when MEMBRANE_JWT_ISSUER /
MEMBRANE_JWT_AUDIENCE are set. Rotation = change the secret and restart:
every outstanding token dies with it. See docs/auth.md.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field

INTERNAL_SUB = "seat:internal"
INTERNAL_TTL = 3600                 # the seat's own token: an hour, re-minted well before
INTERNAL_REFRESH = 600              # ...it has less than ten minutes left
CHILD_TTL = 30 * 86400              # a launched TUI child lives as long as the seat
MIN_SECRET = 32
LEEWAY = 30
MAX_TOKEN = 8192
DEFAULT_TTL = "30d"
COOKIE = "membrane_jwt"
INVALID = "invalid token"           # the only thing a refused token is ever told
UNAUTHORIZED = "missing or invalid token"
_B64URL = re.compile(r"^[A-Za-z0-9_-]*$")
_SUB_OK = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")


class JWTError(ValueError):
    """A refused token. str() is always 'invalid token'; `reason` is for logs/tests."""

    def __init__(self, reason: str):
        super().__init__(INVALID)
        self.reason = reason


class JWTConfigError(RuntimeError):
    """The JWT configuration is unusable (the seat refuses to boot)."""


@dataclass(frozen=True)
class Identity:
    sub: str
    name: str | None = None
    claims: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def label(self) -> str:
        return self.name or self.sub

    @property
    def internal(self) -> bool:
        return self.sub == INTERNAL_SUB


@dataclass(frozen=True)
class Config:
    secret: bytes
    issuer: str | None = None
    audience: str | None = None


# -- encoding -------------------------------------------------------------------

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    """Strict base64url: the URL-safe alphabet only, no padding."""
    if not _B64URL.match(s) or len(s) % 4 == 1:
        raise JWTError("encoding")
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except (binascii.Error, ValueError):
        raise JWTError("encoding") from None


def _check_secret(secret) -> bytes:
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not isinstance(secret, (bytes, bytearray)) or len(secret) < MIN_SECRET:
        raise JWTConfigError(f"the JWT secret must be at least {MIN_SECRET} bytes "
                             "(MEMBRANE_JWT_SECRET / MEMBRANE_JWT_SECRET_FILE); "
                             "generate one with: python3 -c 'import secrets; print(secrets.token_urlsafe(48))'")
    return bytes(secret)


def _sign(secret: bytes, signing_input: bytes) -> bytes:
    return hmac.new(secret, signing_input, hashlib.sha256).digest()


# -- mint / verify --------------------------------------------------------------

def mint(secret, sub: str, name: str | None = None, ttl: int = 3600, extra: dict | None = None,
         now: float | None = None, issuer: str | None = None, audience: str | None = None) -> str:
    """A signed HS256 token for `sub` (expires in `ttl` seconds)."""
    key = _check_secret(secret)
    if not isinstance(sub, str) or not _SUB_OK.match(sub):
        raise ValueError("sub must be a non-empty string without control characters")
    if int(ttl) <= 0:
        raise ValueError("ttl must be positive")
    t = int(now if now is not None else time.time())
    claims = dict(extra or {})
    claims.update({"sub": sub, "iat": t, "exp": t + int(ttl)})
    if name:
        claims["name"] = str(name)
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    head = _b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64e(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64e(_sign(key, f"{head}.{body}".encode("ascii")))
    return f"{head}.{body}.{sig}"


def _time_claim(claims: dict, name: str) -> float | None:
    """A NumericDate claim as a float; None when absent or not a number (a
    bool, a string, null -- the caller refuses)."""
    v = claims.get(name)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def verify(token, secret, issuer: str | None = None, audience: str | None = None,
           leeway: int = LEEWAY, now: float | None = None) -> Identity:
    """The token's identity, or JWTError('invalid token')."""
    key = _check_secret(secret)
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN:
        raise JWTError("shape")
    segs = token.split(".")
    if len(segs) != 3 or not all(segs):
        raise JWTError("shape")
    h64, p64, s64 = segs
    try:
        header = json.loads(_b64d(h64))
    except ValueError as e:
        raise JWTError(getattr(e, "reason", "header")) from None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise JWTError("alg")
    if "crit" in header:
        raise JWTError("crit")
    typ = header.get("typ")
    if typ is not None and (not isinstance(typ, str) or typ.upper() != "JWT"):
        raise JWTError("typ")
    sig = _b64d(s64)
    want = _sign(key, f"{h64}.{p64}".encode("ascii"))
    if not hmac.compare_digest(sig, want):
        raise JWTError("signature")
    try:
        claims = json.loads(_b64d(p64))
    except ValueError:
        raise JWTError("payload") from None
    if not isinstance(claims, dict):
        raise JWTError("payload")
    t = now if now is not None else time.time()
    exp = _time_claim(claims, "exp")
    if exp is None or t - leeway >= exp:
        raise JWTError("exp")                # required: an absent or non-numeric exp is refused
    if "nbf" in claims:
        nbf = _time_claim(claims, "nbf")
        if nbf is None or t + leeway < nbf:
            raise JWTError("nbf")
    if "iat" in claims:
        iat = _time_claim(claims, "iat")
        if iat is None or t + leeway < iat:
            raise JWTError("iat")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not _SUB_OK.match(sub):
        raise JWTError("sub")
    if issuer is not None and claims.get("iss") != issuer:
        raise JWTError("iss")
    aud = claims.get("aud")
    if audience is not None:
        auds = aud if isinstance(aud, list) else [aud]
        if audience not in [a for a in auds if isinstance(a, str)]:
            raise JWTError("aud")
    elif aud is not None:
        raise JWTError("aud")              # scoped to someone else (RFC 7519 4.1.3)
    name = claims.get("name")
    name = " ".join(name.split())[:120] if isinstance(name, str) and name.strip() else None
    return Identity(sub, name, claims)


def parse_ttl(s) -> int:
    """'30d' / '12h' / '15m' / '45s' / '3600' -> seconds (positive)."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd]?)\s*", str(s or ""))
    if not m or int(m.group(1)) <= 0:
        raise ValueError(f"bad ttl {s!r}: use e.g. 30d, 12h, 15m, 45s or seconds")
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


# -- configuration --------------------------------------------------------------

_file_cache: dict = {}


def _read_secret_file(path: str) -> bytes:
    try:
        st = os.stat(path)
        k = (path, st.st_mtime_ns, st.st_size)
        if k not in _file_cache:
            with open(path, "rb") as f:
                data = f.read()
            _file_cache.clear()
            _file_cache[k] = data.rstrip(b"\r\n")      # a k8s Secret / echo's trailing newline
        return _file_cache[k]
    except OSError as e:
        raise JWTConfigError(f"MEMBRANE_JWT_SECRET_FILE is not readable ({e.strerror})") from None


def config() -> Config | None:
    """The JWT configuration from the environment, or None (JWT mode off).
    JWTConfigError when it is set but unusable -- fail closed, never open."""
    env = os.environ
    has_s, has_f = "MEMBRANE_JWT_SECRET" in env, "MEMBRANE_JWT_SECRET_FILE" in env
    if not (has_s or has_f):
        return None
    if has_s and has_f:
        raise JWTConfigError("set MEMBRANE_JWT_SECRET or MEMBRANE_JWT_SECRET_FILE, not both")
    raw = env["MEMBRANE_JWT_SECRET"].encode("utf-8") if has_s \
        else _read_secret_file(env["MEMBRANE_JWT_SECRET_FILE"])
    if not raw:
        raise JWTConfigError("MEMBRANE_JWT_SECRET is set but empty: unset it to turn JWT mode off")
    secret = _check_secret(raw)
    return Config(secret, env.get("MEMBRANE_JWT_ISSUER") or None, env.get("MEMBRANE_JWT_AUDIENCE") or None)


def enabled() -> bool:
    """JWT mode on? (A broken configuration counts as on: the gate fails closed.)"""
    try:
        return config() is not None
    except JWTConfigError:
        return True


def boot_check() -> Config | None:
    """At boot: the configuration, or SystemExit with the reason (a seat never
    starts half-protected)."""
    try:
        cfg = config()
    except JWTConfigError as e:
        raise SystemExit(f"membrane: JWT mode refused to start: {e}") from None
    if cfg is not None:
        internal_token()
        print("[auth] JWT mode ON: every request on both ports needs a token "
              f"(HS256{', iss ' + cfg.issuer if cfg.issuer else ''}"
              f"{', aud ' + cfg.audience if cfg.audience else ''})", file=sys.stderr)
    return cfg


def verify_configured(token: str, cfg: Config | None = None) -> Identity:
    cfg = cfg or config()
    if cfg is None:
        raise JWTError("off")
    return verify(token, cfg.secret, issuer=cfg.issuer, audience=cfg.audience)


# -- the seat's own token -------------------------------------------------------

_internal = {"key": None, "token": None, "exp": 0.0}
_internal_lock = threading.Lock()


def internal_token() -> str | None:
    """The seat's in-memory token (sub 'seat:internal'), re-minted before it
    expires or when the secret changes. None when JWT mode is off."""
    try:
        cfg = config()
    except JWTConfigError:
        return None
    if cfg is None:
        return None
    now = time.time()
    key = (cfg.secret, cfg.issuer, cfg.audience)
    with _internal_lock:
        if _internal["key"] != key or _internal["exp"] - now < INTERNAL_REFRESH:
            _internal["token"] = mint(cfg.secret, INTERNAL_SUB, ttl=INTERNAL_TTL, now=now,
                                      issuer=cfg.issuer, audience=cfg.audience)
            _internal["key"], _internal["exp"] = key, now + INTERNAL_TTL
        return _internal["token"]


def internal_headers() -> dict:
    """{'Authorization': 'Bearer <internal>'} for a loopback call, {} when off."""
    tok = internal_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def client_headers() -> dict:
    """A client process's headers (the MCP child): an explicit
    MEMBRANE_JWT_TOKEN first, else the internal token minted from the
    inherited secret, else nothing."""
    tok = os.environ.get("MEMBRANE_JWT_TOKEN", "").strip()
    if tok:
        return {"Authorization": f"Bearer {tok}"}
    return internal_headers()


def child_env(env: dict) -> dict:
    """A launched opencode child's env: in JWT mode, a long-lived internal
    token as MEMBRANE_JWT_TOKEN (the generated config's provider apiKey reads
    it) and as OPENCODE_SERVER_PASSWORD (opencode attach's Basic auth). Only
    in memory -- never written into a config file."""
    try:
        cfg = config()
    except JWTConfigError:
        cfg = None
    if cfg is None:
        return env
    tok = mint(cfg.secret, INTERNAL_SUB, ttl=CHILD_TTL, issuer=cfg.issuer, audience=cfg.audience)
    out = dict(env)
    out["MEMBRANE_JWT_TOKEN"] = tok
    out["OPENCODE_SERVER_PASSWORD"] = tok
    return out


def provider_options(opts: dict) -> dict:
    """The generated opencode config's provider options: in JWT mode the
    apiKey is opencode's env placeholder for the child's token; off = as is."""
    return dict(opts, apiKey="{env:MEMBRANE_JWT_TOKEN}") if enabled() else opts


# -- reading a request ----------------------------------------------------------

def _all(headers, name: str) -> list[str]:
    """Every value of a header (a repeated one is ambiguous to the caller)."""
    if hasattr(headers, "get_all"):
        return [str(v) for v in (headers.get_all(name) or [])]
    v = headers.get(name)
    return [] if v is None else [str(v)]


def _from_authorization(value: str, basic: bool) -> str | None:
    scheme, _, cred = value.strip().partition(" ")
    cred = cred.strip()
    if not cred or any(c.isspace() for c in cred):
        return None
    if scheme.lower() == "bearer":
        return cred
    if basic and scheme.lower() == "basic":
        try:
            pair = base64.b64decode(cred, validate=True).decode("utf-8")
        except (binascii.Error, ValueError):
            return None
        _user, sep, pw = pair.partition(":")
        return pw if sep and pw else None
    return None


def _from_cookie(headers) -> str | None:
    found = []
    for v in _all(headers, "Cookie"):
        for kv in str(v or "").split(";"):
            k, sep, val = kv.strip().partition("=")
            if sep and k.strip() == COOKIE:
                found.append(val.strip())
    return found[0] if len(found) == 1 and found[0] else None    # two of them: ambiguous, refused


def token_of(headers, cookie: bool = False, basic: bool = False) -> str | None:
    """The presented token. An Authorization header decides alone (a bad one
    never falls back to the cookie)."""
    auths = _all(headers, "Authorization")
    if len(auths) > 1:
        return None                          # two Authorization headers: ambiguous, refused
    if auths:
        return _from_authorization(auths[0], basic)
    return _from_cookie(headers) if cookie else None


def authenticate(headers, cookie: bool = False, basic: bool = False) -> Identity | None:
    """The request's identity or None. JWTConfigError when misconfigured."""
    cfg = config()
    if cfg is None:
        return None
    tok = token_of(headers, cookie=cookie, basic=basic)
    if not tok:
        return None
    try:
        return verify_configured(tok, cfg)
    except JWTError:
        return None


def _write_json(handler, code: int, obj: dict, extra: dict | None = None) -> None:
    b = json.dumps(obj).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(b)))
    handler.send_header("Cache-Control", "no-store")
    for k, v in (extra or {}).items():
        handler.send_header(k, v)
    handler.send_header("Connection", "close")       # the unread body must not poison keep-alive
    handler.close_connection = True
    handler.end_headers()
    if handler.command != "HEAD":
        handler.wfile.write(b)


def send_unauthorized(handler) -> None:
    _write_json(handler, 401, {"error": {"message": UNAUTHORIZED, "type": "unauthorized"}},
                {"WWW-Authenticate": 'Bearer realm="membrane"'})


def gate(handler, cookie: bool = False, basic: bool = False, public: bool = False) -> bool:
    """The one door: True = carry on (JWT off, a valid token, or a public
    path); False = answered here (401, or 503 when misconfigured).
    Sets handler._identity on every request (keep-alive: never stale)."""
    handler._identity = None
    try:
        if config() is None:
            return True
        ident = authenticate(handler.headers, cookie=cookie, basic=basic)
    except JWTConfigError:
        _write_json(handler, 503, {"error": {"message": "authentication is misconfigured",
                                             "type": "unavailable"}})
        return False
    handler._identity = ident
    if ident is not None or public:
        return True
    send_unauthorized(handler)
    return False


def request_is_https(headers) -> bool:
    xfp = str(headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    if xfp == "https":
        return True
    fwd = str(headers.get("Forwarded") or "").lower()
    return bool(re.search(r"(^|[;,\s])proto=\"?https\"?", fwd))


def login_cookie(token: str, ident: Identity, secure: bool) -> str:
    age = max(0, int(ident.claims.get("exp", 0) - time.time()))
    return (f"{COOKIE}={token}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict"
            + ("; Secure" if secure else ""))


def logout_cookie(secure: bool) -> str:
    return f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" + ("; Secure" if secure else "")


# -- attribution ----------------------------------------------------------------

def stamp_text(text: str, ident: Identity) -> str:
    from .speakers import id_line, strip_speaker_lines
    return id_line(ident.sub, ident.name) + "\n" + strip_speaker_lines(str(text or ""))


def stamp_chat_body(body: dict, ident: Identity | None) -> dict:
    """An OpenAI chat body with every user message's speaker line(s) replaced
    by the token's. The internal identity (or none) leaves it untouched."""
    if ident is None or ident.internal or not isinstance(body, dict):
        return body
    from .speakers import id_line, strip_speaker_lines
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body
    out = []
    for m in msgs:
        if not (isinstance(m, dict) and m.get("role") == "user"):
            out.append(m)
            continue
        c = m.get("content")
        if isinstance(c, list):
            parts = [dict(p, text=strip_speaker_lines(str(p.get("text") or "")))
                     if isinstance(p, dict) and p.get("type") == "text" else p for p in c]
            parts.insert(0, {"type": "text", "text": id_line(ident.sub, ident.name)})
            out.append(dict(m, content=parts))
        else:
            out.append(dict(m, content=stamp_text(c if isinstance(c, str) else "", ident)))
    return dict(body, messages=out)


# -- the CLI: membrane token mint ----------------------------------------------

def cli_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="membrane token",
                                 description="mint a token for a JWT-mode seat (docs/auth.md)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    m = sp.add_parser("mint", help="print a signed token (reads MEMBRANE_JWT_SECRET[_FILE])")
    m.add_argument("--sub", required=True, help="the person's stable id (their memory is keyed by it)")
    m.add_argument("--name", default=None, help="the name the seat calls them")
    m.add_argument("--ttl", default=DEFAULT_TTL, help="lifetime: 30d, 12h, 15m, 45s (default 30d)")
    a = ap.parse_args(argv)
    try:
        cfg = config()
    except JWTConfigError as e:
        print(f"membrane token: {e}", file=sys.stderr)
        return 2
    if cfg is None:
        print("membrane token: no MEMBRANE_JWT_SECRET or MEMBRANE_JWT_SECRET_FILE set "
              "(the seat's secret signs the token)", file=sys.stderr)
        return 2
    if a.sub.startswith("seat:"):
        print("membrane token: 'seat:' ids are the seat's own (reserved)", file=sys.stderr)
        return 2
    try:
        ttl = parse_ttl(a.ttl)
        tok = mint(cfg.secret, a.sub, name=a.name, ttl=ttl, issuer=cfg.issuer, audience=cfg.audience)
    except ValueError as e:
        print(f"membrane token: {e}", file=sys.stderr)
        return 2
    print(tok)
    return 0
