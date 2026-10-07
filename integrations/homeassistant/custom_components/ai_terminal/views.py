"""The ai_terminal asset view (panel.js with a pinned content type)."""
from __future__ import annotations

import os

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS


class AssetView(HomeAssistantView):
    """The panel assets, with EXPLICIT content types.

    Why a view instead of a static path: HA's static serving guessed
    text/plain for .js/.css on this box and the browser blocked the
    response (X-Content-Type-Options: nosniff). Content types are
    load-bearing here; they are pinned, not guessed."""

    url = "/api/ai_terminal/www/{asset:.+}"
    name = "api:ai_terminal:asset"
    requires_auth = False          # public assets (no secrets in JS/CSS)

    _TYPES = {
        ".js": "application/javascript",
        ".css": "text/css",
        ".html": "text/html",
        ".map": "application/json",
    }

    async def get(self, request, asset: str = ""):
        # HA's HomeAssistantView wrapper passes URL match groups as KWARGS
        # (handler(request, **match_info)) -- the live exhibit:
        # "AssetView.get() got an unexpected keyword argument 'asset'".
        asset = (asset or "").strip("/")
        if not asset or "/" in asset or ".." in asset:   # traversal guard
            return web.Response(status=404, text="no such asset")
        path = os.path.join(os.path.dirname(__file__), "www", asset)
        if not os.path.isfile(path):
            return web.Response(status=404, text="no such asset")
        ext = os.path.splitext(asset)[1].lower()
        ctype = self._TYPES.get(ext)
        if ctype is None:
            return web.Response(status=404, text="no such asset")
        body = await request.app[KEY_HASS].async_add_executor_job(
            _read_bytes, path)               # no blocking open on the loop
        return web.Response(body=body, content_type=ctype)


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()
