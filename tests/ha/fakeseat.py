"""A fake seat on 127.0.0.1 for the component's tests: the routes the component calls, with the door's own 401 (the same words and
header cliffvm sends) for a token it does not hold. Shapes follow tests/golden/session. Only ever listens on loopback."""
from __future__ import annotations

import base64

from aiohttp import web

BODY = web.RequestKey("body", object)
UNAUTH = {"error": {"message": "missing or invalid token", "type": "unauthorized"}}


class FakeSeat:
    def __init__(self, token="tok-good"):
        self.token = token
        self.seen: list[tuple[str, str, dict, object]] = []     # (method, path, headers, json body)
        self.sessions: dict[str, list] = {}
        self.turn_error: str | None = None                       # the cause a failed turn carries
        self.status_override: int | None = None                  # an answer for every route (a seat that is down: 503)
        self.reply = lambda text: f"Done: {text}"

    def _pw(self, req):
        h = req.headers.get("Authorization", "")
        if h.startswith("Basic "):
            return base64.b64decode(h[6:]).decode().partition(":")[2]
        if h.startswith("Bearer "):
            return h[7:]
        return None

    @web.middleware
    async def _gate(self, req, handler):
        body = None
        if req.can_read_body:
            try:
                body = await req.json()
            except Exception:                          # noqa: BLE001
                body = None
        self.seen.append((req.method, req.path, dict(req.headers), body))
        req[BODY] = body
        if self.status_override:
            return web.json_response({"error": "down"}, status=self.status_override)
        if self._pw(req) != self.token:
            return web.json_response(UNAUTH, status=401, headers={"WWW-Authenticate": 'Bearer realm="membrane"'})
        return await handler(req)

    async def _health(self, req):
        return web.json_response({"healthy": True, "version": "9.9"})

    async def _create(self, req):
        sid = f"ses_{len(self.sessions) + 1:024d}"
        self.sessions[sid] = []
        return web.json_response({"id": sid, "title": (req[BODY] or {}).get("title", "")})

    async def _message(self, req):
        sid = req.match_info["sid"]
        if sid not in self.sessions:
            return web.json_response({"type": "error", "error": {"message": "not found"}}, status=404)
        parts = req[BODY]["parts"]
        text = "\n".join(p["text"] for p in parts if p.get("type") == "text")
        self.sessions[sid].append(text)
        info = {"role": "assistant", "error": None}
        out = [{"type": "text", "text": self.reply(text)}]
        if self.turn_error:
            info["error"] = {"name": "Error", "data": {"message": self.turn_error}}
            out = [{"type": "text", "text": self.turn_error}]
        return web.json_response({"info": info, "parts": out})

    async def __aenter__(self):
        app = web.Application(middlewares=[self._gate])
        app.router.add_get("/global/health", self._health)
        app.router.add_post("/session", self._create)
        app.router.add_post("/session/{sid}/message", self._message)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{self.port}"
        return self

    async def __aexit__(self, *a):
        await self._runner.cleanup()
