"""The UI's files and small reads (ported from the boat's ai_terminal/views.py).

THE UI IS THE PACKAGE'S: the same files `membrane standalone` serves, read
from the installed membrane package (membrane/ui/) -- one UI, two hosts. The
package copy speaks standalone's routes (/ui/www, /ui/serve, /ui/stt,
/ui/conf); served here they become this integration's (/api/membrane/...).
The big voice models live in <config>/membrane/assets/ (fetched on first use).
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.http import KEY_HASS

from .const import DOMAIN, SEAT_DIR

HERE = Path(__file__).resolve().parent / "www"          # the integration's own (entry.js)
REWRITE = ((b'"/ui/www/', b'"/api/membrane/www/'), (b'"/ui/serve', b'"/api/membrane/serve'),
           (b'"/ui/stt', b'"/api/membrane/stt'), (b'"/ui/conf', b'"/api/membrane/conf'))
TYPES = {".js": "application/javascript", ".mjs": "application/javascript", ".css": "text/css",
         ".html": "text/html", ".map": "application/json", ".gz": "application/gzip",
         ".wasm": "application/wasm", ".svg": "image/svg+xml", ".json": "application/json"}
BIG = (".wasm", ".gz")


def package_ui() -> Path | None:
    spec = importlib.util.find_spec("membrane")
    locs = list(getattr(spec, "submodule_search_locations", None) or []) if spec else []
    for loc in locs:
        p = Path(loc) / "ui"
        if p.is_dir():
            return p
    return None


def find(hass, name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    dirs = [HERE, package_ui(), Path(hass.config.path(SEAT_DIR)) / "assets"]
    for d in dirs:
        if d is not None and (d / name).is_file():
            return d / name
    return None


def _read(path: Path) -> bytes:
    return path.read_bytes()


class AssetView(HomeAssistantView):
    """The UI's files, with pinned content types (HA guessed text/plain for .js
    on the boat and the browser refused it)."""

    url = "/api/membrane/www/{asset:.+}"
    name = "api:membrane:asset"
    requires_auth = False          # code and models, no secrets

    async def get(self, request, asset: str = ""):
        hass = request.app[KEY_HASS]
        asset = (asset or "").strip("/")
        path = await hass.async_add_executor_job(find, hass, asset)
        ctype = TYPES.get(os.path.splitext(asset)[1].lower())
        if path is None or ctype is None:
            return web.Response(status=404, text="no such asset")
        body = await hass.async_add_executor_job(_read, path)
        if path.suffix in (".js", ".mjs") and path.parent != HERE:
            for a, b in REWRITE:
                body = body.replace(a, b)
        cache = "public, max-age=604800" if path.suffix in BIG else "no-cache"
        return web.Response(body=body, content_type=ctype, headers={"Cache-Control": cache})


def _name(hass) -> dict:
    comp = hass.data.get(DOMAIN) or {}
    return {"name": comp.get("name") or "Membrane", "aliases": list(comp.get("aliases") or [])}


class NameView(HomeAssistantView):
    """The seat's name, before anyone signs in (entry.js sets it before the UI
    loads: the labels and "Hey <name>" are read at module load). The name only."""

    url = "/api/membrane/name"
    name = "api:membrane:name"
    requires_auth = False

    async def get(self, request):
        return self.json(_name(request.app[KEY_HASS]))


class ConfView(HomeAssistantView):
    """The chat panel's config: is the seat there."""

    url = "/api/membrane/conf"
    name = "api:membrane:conf"
    requires_auth = True

    async def get(self, request):
        comp = request.app[KEY_HASS].data.get(DOMAIN) or {}
        return self.json({"serve_url": "membrane (inside Home Assistant)" if comp.get("inside") else comp.get("serve_url", ""),
                          "serve_ok": bool(comp.get("serve_url") and comp.get("serve_pass")),
                          **_name(request.app[KEY_HASS])})
