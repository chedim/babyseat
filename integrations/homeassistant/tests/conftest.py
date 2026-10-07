"""Test bootstrap: stub the Home Assistant + aiohttp surface.

The integration's import-time dependencies are small and stable. A minimal
fake package lets the pure-logic modules (panel_config,
serve_proxy, views) import and unit-test without a HA install.
The assertions pin LOGIC contracts, not HA internals.
"""
from __future__ import annotations

import sys
import types


def _install_stubs() -> None:
    # ---- aiohttp (the real one is absent on the mac; views only need
    # web + client shapes at import time) ----
    if "aiohttp" not in sys.modules:
        aiohttp = types.ModuleType("aiohttp")
        web = types.ModuleType("aiohttp.web")

        class _WSMsgType:
            BINARY = "binary"
            TEXT = "text"
            CLOSE = "close"
            ERROR = "error"

        class WebSocketResponse:
            def __init__(self, protocols=None):
                self.protocols = protocols

        class Response:
            def __init__(self, *a, status=200, text=None, body=None, **k):
                self.status = status
                self.text = text
                self.body = body

        class StreamResponse:
            def __init__(self, *a, **k):
                self.status = k.get("status", 200)

            async def prepare(self, request):
                return self

            async def write(self, data):
                return None

            async def write_eof(self, *a):
                return None

        web.WebSocketResponse = WebSocketResponse
        web.WSMsgType = _WSMsgType
        web.Response = Response
        web.StreamResponse = StreamResponse
        aiohttp.web = web

        class _ClientTimeout:
            def __init__(self, *a, total=None, sock_read=None, **k):
                self.total = total

        class _ClientError(Exception):
            pass

        class _ClientSession:
            closed = False

            def __init__(self, *a, **k):
                pass

            async def close(self):
                return None

        aiohttp.ClientTimeout = _ClientTimeout
        aiohttp.ClientError = _ClientError
        aiohttp.ClientSession = _ClientSession
        sys.modules["aiohttp"] = aiohttp
        sys.modules["aiohttp.web"] = web

    # ---- homeassistant ----
    if "homeassistant" in sys.modules:
        return                                   # real HA present: use it

    ha = types.ModuleType("homeassistant")
    ha.__path__ = []                             # mark as a package

    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.callback = lambda f: f                  # the decorator is identity

    def _track_time_interval(hass, action, interval):
        return lambda: None

    helpers = types.ModuleType("homeassistant.helpers")
    helpers_event = types.ModuleType("homeassistant.helpers.event")
    helpers_event.async_track_time_interval = _track_time_interval
    helpers_http = types.ModuleType("homeassistant.helpers.http")
    helpers_http.KEY_HASS = "hass"               # an AppKey in real HA

    components = types.ModuleType("homeassistant.components")
    http = types.ModuleType("homeassistant.components.http")

    class StaticPathConfig:                      # the dataclass-ish shape
        def __init__(self, url_path, path, cache_headers=True):
            self.url_path = url_path
            self.path = path
            self.cache_headers = cache_headers

    class HomeAssistantView:                     # the view base
        requires_auth = True
        url = None
        name = None

        @staticmethod
        def json(value, status_code=200):
            return ("json", value, status_code)

    http.StaticPathConfig = StaticPathConfig
    http.HomeAssistantView = HomeAssistantView

    fe = types.ModuleType("homeassistant.components.frontend")
    fe.add_extra_js_url = lambda hass, url: None
    fe.remove_extra_js_url = lambda hass, url: None
    fe.async_register_built_in_panel = lambda *a, **k: None
    fe.async_remove_panel = lambda *a, **k: None

    components.http = http
    components.frontend = fe
    helpers.event = helpers_event
    helpers.http = helpers_http

    ha.core = core
    ha.helpers = helpers
    ha.components = components

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.event"] = helpers_event
    sys.modules["homeassistant.helpers.http"] = helpers_http
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.http"] = http
    sys.modules["homeassistant.components.frontend"] = fe


_install_stubs()
