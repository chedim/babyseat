"""The membrane-serve proxy: the dashboard's direct lane to the brain.

The panel (running inside HA's authed frontend) speaks membrane's native
serve surface (the opencode-compatible REST of membrane/server.py)
through THIS proxy riding HA's own aiohttp server:

    client (panel, HA bearer auth)
      -> /api/ai_terminal/serve/<rest>   (this view; requires_auth=True)
      -> http://<serve_url>/<rest>       (basic auth from the yaml pair)

Why a proxy and not a direct browser call to the brain: the serve's
basic-auth pair would have to ship to the browser, and HA is the one
origin reachable from the LAN AND from outside (chegate *.agatha.boats ->
tunnel -> :8123); the brain phone is LAN-only. The proxy keeps zero new
public endpoints and keeps the serve credential server-side.

PER-USER SESSIONS: the brain has ONE identity (the serve's basic-auth
pair), so ownership lives HERE: every session created through the proxy
is recorded against the HA user who created it (an HA Store,
`ai_terminal.sessions`). The session list and status are filtered to the
caller's own; any other session id is a 404.

THE LIVE LANE (2026-10-08): the serve's /event SSE broadcasts every
session's events; GET serve/event relays it PER USER -- a frame passes
only when it names one of the caller's sessions (or is the stream's
`server.connected` hello). Other users' chats, the watch loops' sessions
and session-less internals never leave HA. The owners map is read live,
so a chat created after the stream opened streams too.

THE SPEAKER: each message the proxy forwards gets a first line
`[HA user: <name>]` (SPEAKER_PREFIX) -- membrane's field learns who said
what; the panel hides the line when rendering history.
"""
from __future__ import annotations

import base64
import json
import logging

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

from .const import PROXY_TIMEOUT_S

_LOGGER = logging.getLogger(__name__)

# The allow-list (prefix rules). The serve is basic-authed already; this
# keeps the proxy from being an open relay while the surface stays the
# chat-client subset (+ the health probe the panel uses as its check).
ALLOWED_PREFIXES = ("session", "config", "provider", "mcp")
# agent: the primary agents + membrane's live minds (loop workers,
# thinker, dreamer); event: the per-user SSE relay (never global/event,
# its unscoped twin)
ALLOWED_EXACT = ("global/health", "agent", "event")
# the field dashboard + the drawer: the seat's read surfaces the serve
# relays (membrane/server.py SEAT_READ_PATHS) -- GET only, exact paths
READ_ONLY_EXACT = ("membrane/state", "membrane/map/field",
                   "membrane/field/entity", "membrane/map/distance",
                   "membrane/map/field3d", "membrane/map/tile",
                   "membrane/map/locate", "v1/models",
                   "membrane/entity/read", "membrane/entity-related")
EVENT_HELLO = "server.connected"

SPEAKER_PREFIX = "[HA user: "
OWNERS_STORE_KEY = "ai_terminal.sessions"
OWNERS_STORE_VERSION = 1


def allowed_path(rest: str) -> bool:
    """Pure rule for tests: may the proxy forward this serve sub-path."""
    rest = (rest or "").strip("/")
    if not rest:
        return False
    if rest in ALLOWED_EXACT or rest in READ_ONLY_EXACT:
        return True
    return any(rest == p or rest.startswith(p + "/")
               for p in ALLOWED_PREFIXES)


def read_only_path(rest: str) -> bool:
    """The field reads: forwarded on GET only (any other method = 405)."""
    return (rest or "").strip("/") in READ_ONLY_EXACT


def message_body(text: str, agent: str = "build") -> dict:
    """The serve POST /session/{sid}/message payload (text part only)."""
    return {"agent": agent, "parts": [{"type": "text", "text": text}]}


def parts_text(row: dict) -> str:
    """The visible text of one message ROW ({info, parts}): the text
    parts joined; tool parts surface nothing."""
    parts = row.get("parts") or [] if isinstance(row, dict) else []
    out = []
    for p in parts:
        if isinstance(p, dict) and p.get("type") == "text":
            t = p.get("text") or ""
            if t:
                out.append(t)
    return "\n".join(out)


def reply_of(result: dict) -> str:
    """The assistant reply from a sync POST .../message response
    ({info, parts}) -- the text parts joined."""
    if not isinstance(result, dict):
        return ""
    return parts_text(result)


def speaker_line(name: str) -> str:
    """The first line every forwarded message carries."""
    clean = " ".join(str(name or "unknown").split()).replace("]", ")")
    return f"{SPEAKER_PREFIX}{clean}]"


def with_speaker(body: dict, name: str) -> dict:
    """The message body with the speaker as its FIRST text part (the
    serve joins text parts with newlines, so the model reads it as the
    message's first line). A client-sent speaker line is replaced, never
    trusted: the HA session is the only source of identity."""
    parts = [p for p in (body.get("parts") or [])
             if not (isinstance(p, dict) and p.get("type") == "text"
                     and str(p.get("text") or "").startswith(SPEAKER_PREFIX))]
    out = dict(body)
    out["parts"] = [{"type": "text", "text": speaker_line(name),
                     "synthetic": True}] + parts
    return out


def owned_sessions(sessions: list, owners: dict, user_id: str) -> list:
    """The caller's sessions, most recently active first."""
    mine = [s for s in sessions or []
            if isinstance(s, dict) and owners.get(s.get("id")) == user_id]
    return sorted(mine, key=lambda s: -int(((s.get("time") or {})
                                            .get("updated")) or 0))


def owned_status(status: dict, owners: dict, user_id: str) -> dict:
    """/session/status filtered to the caller's sessions."""
    return {sid: st for sid, st in (status or {}).items()
            if owners.get(sid) == user_id}


def event_session_id(ev) -> str | None:
    """The session an SSE frame ({id, type, properties}) belongs to: the
    serve puts it at properties.sessionID, or under info / part
    (session.deleted, message.updated, message.part.updated)."""
    if not isinstance(ev, dict):
        return None
    props = ev.get("properties")
    if not isinstance(props, dict):
        return None
    sid = props.get("sessionID")
    if sid:
        return str(sid)
    for k in ("info", "part"):
        sub = props.get(k)
        if isinstance(sub, dict) and sub.get("sessionID"):
            return str(sub["sessionID"])
    return None


LIVE_FIELD_PREFIXES = ("field.", "mind.")


def visible_event(ev, owners: dict, user_id: str, is_admin: bool = False) -> bool:
    """May this frame reach the caller: the stream's hello, or an event of
    a session the caller owns. Session-less frames stay inside HA -- except
    the field viewer's live layer (field.* / mind.*: entity ids, kinds and
    tool names, never message text), which reaches HA ADMINS only."""
    if isinstance(ev, dict) and ev.get("type") == EVENT_HELLO:
        return True
    if isinstance(ev, dict) and str(ev.get("type") or "").startswith(LIVE_FIELD_PREFIXES):
        return bool(is_admin)
    sid = event_session_id(ev)
    return bool(user_id and sid and owners.get(sid) == user_id)


class SSEFrames:
    """Incremental SSE parser: feed raw bytes, get the complete JSON frames
    (comments/keepalives skipped, multi-line data joined, chunk splits
    held until the blank line, undecodable frames dropped)."""

    def __init__(self):
        self._buf = b""

    def feed(self, chunk: bytes) -> list:
        self._buf += chunk.replace(b"\r\n", b"\n")
        out = []
        while b"\n\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n\n", 1)
            data = [line[5:].lstrip(b" ") for line in raw.split(b"\n")
                    if line.startswith(b"data:")]
            if not data:
                continue
            try:
                ev = json.loads(b"\n".join(data))
            except ValueError:
                continue
            if isinstance(ev, dict):
                out.append(ev)
        return out


def basic_header(user: str, password: str) -> str:
    """The Authorization header value for the serve basic auth."""
    raw = f"{user or ''}:{password or ''}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def component_of(request) -> dict:
    """The integration's hass.data slot off a view request (HA keeps the
    instance at request.app[KEY_HASS]; there is no request.hass)."""
    return request.app[KEY_HASS].data.get("ai_terminal") or {}


async def _owners_of(request) -> tuple[dict, object]:
    """The {session_id: ha_user_id} map + its Store (lazy-loaded once)."""
    comp = component_of(request)
    if "owners" not in comp:
        from homeassistant.helpers.storage import Store
        store = Store(request.app[KEY_HASS], OWNERS_STORE_VERSION,
                      OWNERS_STORE_KEY)
        comp["owners"] = (await store.async_load() or {}).get("owners", {})
        comp["owners_store"] = store
    return comp["owners"], comp["owners_store"]


def _save_owners(owners: dict, store) -> None:
    store.async_delay_save(lambda: {"owners": owners}, 2.0)


def _json_response(status: int, data) -> web.Response:
    return web.Response(status=status, body=json.dumps(data).encode(),
                        content_type="application/json")


class ServeProxyView(HomeAssistantView):
    """Proxy the allowed methods/paths onto the membrane serve, scoped to
    the calling HA user's own sessions."""

    url = "/api/ai_terminal/serve/{path:.+}"
    name = "api:ai_terminal:serve"
    requires_auth = True             # HA bearer/session, like any /api view

    async def _session_of(self, comp):
        sess = comp.get("http_session")
        if sess is None or sess.closed:
            # a fresh connection per request: the brain is a stdlib
            # http.server on the LAN; pooled keep-alive buys nothing there
            sess = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(force_close=True))
            comp["http_session"] = sess
        return sess

    async def _upstream(self, request, comp, rest, body):
        """One call to the serve -> (status, payload bytes, content type)
        or a ready 502 Response."""
        headers = {"Authorization": basic_header(comp.get("serve_user"),
                                                 comp.get("serve_pass"))}
        if body is not None:
            headers["Content-Type"] = "application/json"
        url = comp["serve_url"].rstrip("/") + "/" + rest
        sess = await self._session_of(comp)
        try:
            async with sess.request(
                    request.method, url, headers=headers,
                    params=request.query, data=body,
                    timeout=aiohttp.ClientTimeout(
                        total=PROXY_TIMEOUT_S)) as up:
                payload = await up.read()
                return (up.status, payload,
                        up.headers.get("Content-Type", "application/json"))
        except aiohttp.ClientError as e:
            _LOGGER.warning("ai_terminal: %s %s -> %r", request.method,
                            rest, e)
            return web.Response(status=502,
                                text=f"serve unreachable: {e!r}")
        except Exception as e:                          # noqa: BLE001
            _LOGGER.exception("ai_terminal: %s %s failed", request.method,
                              rest)
            return web.Response(status=502, text=f"serve error: {e!r}")

    async def _stream_events(self, request, comp, uid, owners, is_admin=False):
        """GET serve/event: the brain's /event, filtered to the caller's
        sessions, as a live text/event-stream. Ends when either side
        hangs up; the panel reconnects (and resyncs over REST)."""
        resp = web.StreamResponse(status=200, headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            # nginx (agdroid :8123 and chegate) must not buffer the stream
            "X-Accel-Buffering": "no",
        })
        await resp.prepare(request)
        headers = {"Authorization": basic_header(comp.get("serve_user"),
                                                 comp.get("serve_pass")),
                   "Accept": "text/event-stream"}
        url = comp["serve_url"].rstrip("/") + "/event"
        sess = await self._session_of(comp)
        parser = SSEFrames()
        try:
            async with sess.request(
                    "GET", url, headers=headers,
                    # no total cap; the serve pings every 15s, so a minute
                    # of silence means the brain is gone
                    timeout=aiohttp.ClientTimeout(total=None,
                                                  sock_read=60)) as up:
                if up.status != 200:
                    _LOGGER.warning("ai_terminal: event stream upstream %s",
                                    up.status)
                    return resp
                async for chunk in up.content.iter_any():
                    for ev in parser.feed(chunk):
                        if visible_event(ev, owners, uid, is_admin):
                            await resp.write(b"data: " + json.dumps(
                                ev).encode() + b"\n\n")
                    if b": ping" in chunk:
                        # keepalive for every hop (nginx read timeouts)
                        await resp.write(b": ping\n\n")
        except (aiohttp.ClientError, ConnectionResetError, OSError) as e:
            _LOGGER.debug("ai_terminal: event stream closed: %r", e)
        except Exception as e:                          # noqa: BLE001
            if type(e).__name__ not in ("TimeoutError", "CancelledError"):
                _LOGGER.warning("ai_terminal: event stream failed: %r", e)
            if type(e).__name__ == "CancelledError":
                raise
        return resp

    async def _proxy(self, request):
        comp = component_of(request)
        rest = (request.match_info.get("path") or "").strip("/")
        if not allowed_path(rest):
            return web.Response(status=404, text="serve proxy: path not "
                                "allowed")
        if not comp.get("serve_url") or not comp.get("serve_pass"):
            return web.Response(status=503, text="serve lane not configured "
                                "(ai_terminal: serve_url + serve_pass)")
        user = request.get("hass_user")
        uid = str(getattr(user, "id", "") or "")
        if not uid:
            return web.Response(status=403, text="no HA user on request")
        owners, store = await _owners_of(request)
        segs = rest.split("/")
        method = request.method
        if read_only_path(rest) and method != "GET":
            return web.Response(status=405, text="field reads are GET only")
        if rest == "event":
            if method != "GET":
                return web.Response(status=405, text="event is GET only")
            return await self._stream_events(
                request, comp, uid, owners,
                bool(getattr(user, "is_admin", False)))

        body = None
        if method in ("POST", "PATCH"):
            raw = await request.read()
            try:
                parsed = json.loads(raw) if raw else {}
            except ValueError:
                return web.Response(status=400, text="body must be JSON")
            if (segs[0] == "session" and len(segs) == 3
                    and segs[2] in ("message", "prompt_async")
                    and isinstance(parsed, dict)):
                parsed = with_speaker(parsed, getattr(user, "name", ""))
            body = json.dumps(parsed).encode()

        # a specific session: the caller's own, or it does not exist
        if (segs[0] == "session" and len(segs) >= 2
                and segs[1] != "status" and owners.get(segs[1]) != uid):
            return _json_response(404, {"type": "error", "error": {
                "message": "not found"}})

        res = await self._upstream(request, comp, rest, body)
        if isinstance(res, web.Response):
            return res
        status, payload, ctype = res

        if segs[0] == "session" and status == 200:
            data = None
            try:
                data = json.loads(payload) if payload else None
            except ValueError:
                pass
            if len(segs) == 1 and method == "GET" and isinstance(data, list):
                return _json_response(200, owned_sessions(data, owners, uid))
            if len(segs) == 1 and method == "POST" and isinstance(data, dict) \
                    and data.get("id"):
                owners[data["id"]] = uid
                _save_owners(owners, store)
            if segs[1:] == ["status"] and isinstance(data, dict):
                return _json_response(200, owned_status(data, owners, uid))
            if len(segs) == 2 and method == "DELETE":
                owners.pop(segs[1], None)
                _save_owners(owners, store)
        return web.Response(status=status, body=payload,
                            content_type=ctype.split(";")[0].strip())

    async def get(self, request, path: str = ""):
        return await self._proxy(request)

    async def post(self, request, path: str = ""):
        return await self._proxy(request)

    async def patch(self, request, path: str = ""):
        return await self._proxy(request)

    async def delete(self, request, path: str = ""):
        return await self._proxy(request)


class ConfView(HomeAssistantView):
    """The panel's own tiny config: is the serve lane configured."""

    url = "/api/ai_terminal/conf"
    name = "api:ai_terminal:conf"
    requires_auth = True

    async def get(self, request):
        comp = component_of(request)
        return self.json({
            "serve_url": comp.get("serve_url", ""),
            "serve_ok": bool(comp.get("serve_url")
                             and comp.get("serve_pass")),
        })
