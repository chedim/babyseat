"""The login page's strangers: the ONE public door to the brain (2026-10-10).

HA's login page (injected by nginx, www/stranger.js) shows a small,
label-free piece of membrane's field around the visitor's own entity
(`strangerN`, minted on the seat by membrane/strangers.py). No HA auth is
possible there -- the visitor has not logged in -- so this view is
requires_auth=False and kept narrow:

- four ops only (visit, view, event, leave), GET, every parameter checked
  here before it leaves HA: the token is hex, the kind one of five words,
  counts and motion integers -- nothing a visitor types is forwarded;
- a token bucket per client address; the seat caps the live strangers
  (and per address) and expires them;
- the visitor's address and user agent never reach the seat (it gets a
  salted hash for the per-address cap); they go to a log in HA's config
  directory (ai_terminal_strangers.jsonl), pruned past RETENTION_DAYS.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

from .serve_proxy import basic_header, component_of

_LOGGER = logging.getLogger(__name__)

OPS = ("visit", "view", "event", "leave")
KINDS = ("move", "click", "stay", "focus", "blur")
BUCKET_SIZE = 12          # requests
BUCKET_RATE = 2.0         # refilled per second
TIMEOUT_S = 8.0
RETENTION_DAYS = 30
LOG_NAME = "ai_terminal_strangers.jsonl"
LOG_MAX_BYTES = 2_000_000
_HEX = re.compile(r"^[0-9a-f]{32}$")
_SALT = secrets.token_hex(8)          # per HA run: the hash links nothing across restarts


def client_ip(request) -> str:
    """The visitor's address: HA's own (forwarded-for aware) remote, else
    the first X-Forwarded-For hop nginx added."""
    ip = str(getattr(request, "remote", "") or "")
    if ip in ("", "127.0.0.1", "::1"):
        fwd = str(request.headers.get("X-Forwarded-For", "") or "")
        if fwd:
            ip = fwd.split(",")[0].strip()
    return ip[:64]


def ip_key(ip: str) -> str:
    return hashlib.sha256((_SALT + ip).encode()).hexdigest()[:16]


def clean_params(op: str, query) -> dict | None:
    """What may leave HA for this op, or None (refused)."""
    if op not in OPS:
        return None
    out = {}
    if op != "visit":
        t = str(query.get("t", "") or "")
        if not _HEX.match(t):
            return None
        out["t"] = t
    if op == "event":
        kind = str(query.get("kind", "") or "")
        if kind not in KINDS:
            return None
        out["kind"] = kind
        for k, lo, hi in (("n", 0, 1000), ("dx", -2000, 2000), ("dy", -2000, 2000)):
            v = str(query.get(k, "0") or "0")
            if not re.fullmatch(r"-?\d{1,5}", v):
                return None
            out[k] = str(max(lo, min(hi, int(v))))
    return out


class Bucket:
    def __init__(self, size=BUCKET_SIZE, rate=BUCKET_RATE):
        self.size, self.rate = size, rate
        self._b: dict[str, tuple[float, float]] = {}

    def take(self, key: str, now: float | None = None) -> bool:
        now = now or time.monotonic()
        if len(self._b) > 5000:                       # forget the quiet ones
            self._b = {k: v for k, v in self._b.items() if now - v[1] < 120}
        tokens, ts = self._b.get(key, (float(self.size), now))
        tokens = min(float(self.size), tokens + (now - ts) * self.rate)
        if tokens < 1.0:
            self._b[key] = (tokens, now)
            return False
        self._b[key] = (tokens - 1.0, now)
        return True


def _append_log(path: str, row: dict) -> None:
    try:
        if os.path.exists(path) and os.path.getsize(path) > LOG_MAX_BYTES:
            _prune(path)
        with open(path, "a") as f:
            f.write(json.dumps(row) + "\n")
        os.chmod(path, 0o600)
    except OSError:
        pass


def _prune(path: str) -> None:
    cut = time.time() - RETENTION_DAYS * 86400
    keep = []
    with open(path) as f:
        for line in f:
            try:
                if float(json.loads(line).get("ts") or 0) >= cut:
                    keep.append(line)
            except ValueError:
                continue
    keep = keep[-(len(keep) // 2 or 1):] if sum(map(len, keep)) > LOG_MAX_BYTES else keep
    with open(path, "w") as f:
        f.writelines(keep)


class StrangerView(HomeAssistantView):
    url = "/api/ai_terminal/stranger/{op}"
    name = "api:ai_terminal:stranger"
    requires_auth = False             # the login page: nobody is logged in yet

    def __init__(self):
        self.bucket = Bucket()
        self.names: dict[str, str] = {}               # token -> strangerN (the log's join)

    async def get(self, request, op: str = ""):
        comp = component_of(request)
        if not comp.get("serve_pass"):
            return web.json_response({"ok": False, "error": "off"}, status=503)
        ip = client_ip(request)
        if not self.bucket.take(ip):
            return web.json_response({"ok": False, "error": "slow down"}, status=429)
        params = clean_params(op, request.query)
        if params is None:
            return web.json_response({"ok": False, "error": "bad request"}, status=400)
        if op == "visit":
            params["ip"] = ip_key(ip)
        url = comp["serve_url"].rstrip("/") + "/membrane/stranger/" + op
        headers = {"Authorization": basic_header(comp.get("serve_user"), comp.get("serve_pass"))}
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(force_close=True)) as sess:
                async with sess.get(url, headers=headers, params=params,
                                    timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as up:
                    out = await up.json(content_type=None)
                    status = up.status
        except Exception as e:                          # noqa: BLE001
            _LOGGER.debug("ai_terminal: stranger %s -> %r", op, e)
            return web.json_response({"ok": False, "error": "asleep"}, status=502)
        if not isinstance(out, dict):
            return web.json_response({"ok": False, "error": "asleep"}, status=502)
        if op in ("visit", "leave"):
            hass = request.app[KEY_HASS]
            if op == "visit" and out.get("ok"):
                self.names[str(out.get("t"))] = str(out.get("name"))
                if len(self.names) > 500:
                    self.names = dict(list(self.names.items())[-250:])
            row = {"ts": round(time.time(), 1), "op": op, "ip": ip,
                   "ua": str(request.headers.get("User-Agent", ""))[:300],
                   "lang": str(request.headers.get("Accept-Language", ""))[:80],
                   "name": out.get("name") or self.names.get(params.get("t", ""), ""),
                   "ok": bool(out.get("ok"))}
            await hass.async_add_executor_job(_append_log, hass.config.path(LOG_NAME), row)
        return web.json_response(out, status=status if status < 500 else 502)
