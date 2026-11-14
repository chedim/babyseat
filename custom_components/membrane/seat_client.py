"""The one client of the seat (#1143): an aiohttp session, one auth header, the three calls Assist makes. No Home Assistant import, so
it runs against a real seat in the tests. A seat's door wants a token on every route (docs/auth.md): `Authorization: Basic
<user>:<token>`, the token being the password."""
from __future__ import annotations

import base64

import aiohttp

from .const import (DEFAULT_SERVE_USER, HEALTH_TIMEOUT_S, SESSION_TIMEOUT_S, SESSION_TITLE_PREFIX, TURN_TIMEOUT_S)


class SeatError(Exception):
    """The seat could not do it; `str(e)` is the cause, in the seat's own words where it gave some."""


class SeatAuthError(SeatError):
    """The door answered 401: the token is wrong, signed by another key, or past its life."""


class SeatUnreachable(SeatError):
    """Nothing answered (refused, timed out, not HTTP)."""


class SeatTurnError(SeatError):
    """The seat answered but its turn failed (the CLI's words are the cause)."""


class SeatSessionGone(SeatError):
    """The seat has no such session (its store was reset): the caller makes a new one."""


def speaker_line(name: str) -> str:
    """Who is talking, as the first line of the message: `[HA user: X]` (the line the released proxy sent)."""
    clean = " ".join(str(name or "").split()).replace("]", ")")
    return f"[HA user: {clean}]"


def reply_text(result: dict) -> str:
    """The visible reply of a message result ({info, parts}): the text parts joined; tool parts surface nothing."""
    out = [p["text"] for p in (result or {}).get("parts") or []
           if isinstance(p, dict) and p.get("type") == "text" and p.get("text")]
    return "\n".join(out).strip()


class SeatClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, token: str, user: str = DEFAULT_SERVE_USER) -> None:
        self._s = session
        self.url = str(url or "").strip().rstrip("/")
        self._headers = {"Authorization": "Basic " + base64.b64encode(f"{user or DEFAULT_SERVE_USER}:{token or ''}".encode()).decode()}

    async def _call(self, method: str, path: str, timeout: float, json=None):
        """-> (status, parsed body or None). 401 raises SeatAuthError; a failure to talk raises SeatUnreachable."""
        try:
            async with self._s.request(method, self.url + path, json=json, headers=self._headers,
                                       timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                body = await r.json(content_type=None) if r.status != 204 else None
                status = r.status
        except SeatError:
            raise
        except Exception as e:                       # noqa: BLE001 (refused, timeout, a body that is not JSON)
            raise SeatUnreachable(f"{type(e).__name__}: {e}" if str(e) else type(e).__name__) from e
        if status == 401:
            raise SeatAuthError("the seat refused the token (wrong, or past its life)")
        return status, body

    async def health(self) -> dict:
        status, body = await self._call("GET", "/global/health", HEALTH_TIMEOUT_S)
        if status != 200:
            raise SeatError(f"/global/health answered {status}")
        return body or {}

    async def create_session(self, conversation_id: str) -> str:
        status, body = await self._call("POST", "/session", SESSION_TIMEOUT_S,
                                        {"title": SESSION_TITLE_PREFIX + str(conversation_id)})
        if status != 200 or not isinstance(body, dict) or not body.get("id"):
            raise SeatError(f"session create answered {status}")
        return body["id"]

    async def message(self, sid: str, text: str, speaker: str = "") -> str:
        """One turn on session `sid`: `speaker` (a name) as a first synthetic part `[HA user: X]`, then the text; the answer's text.
        A session the seat does not have: SeatSessionGone."""
        parts = [{"type": "text", "text": text}]
        if speaker:
            parts.insert(0, {"type": "text", "text": speaker_line(speaker), "synthetic": True})
        status, body = await self._call("POST", f"/session/{sid}/message", TURN_TIMEOUT_S, {"agent": "build", "parts": parts})
        if status == 404:
            raise SeatSessionGone(sid)
        if status != 200 or not isinstance(body, dict):
            cause = ((body or {}).get("error") or {}).get("message") if isinstance(body, dict) else None
            raise SeatError(f"the message answered {status}" + (f": {cause}" if isinstance(cause, str) else ""))
        err = ((body.get("info") or {}).get("error") or {}).get("data", {}).get("message") if (body.get("info") or {}).get("error") else None
        if err:
            raise SeatTurnError(str(err))
        return reply_text(body)
