"""Unit tests for the ai_terminal HA integration.

No Home Assistant install needed: the HA surface is stubbed (conftest.py)
and the pure-logic modules are exercised directly. Run:

    .venv/bin/python -m pytest tests/test_ai_terminal.py -q

Pinned contracts:
- the panel config shape (ha-panel-custom.ts:94 reads config._panel_custom;
  a flat dict crashes the loader with "can't access property html_url")
- the asset view's pinned content types + traversal guard
- the chat-only surface: no PTY/tmux lane, nothing spawns membrane here
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components"))

from ai_terminal.panel_config import (  # noqa: E402
    PANEL_ELEMENT,
    panel_custom_config,
    static_paths,
)


class TestPanelContract(unittest.TestCase):
    """THE pinned contract: HA's custom-panel loader (ha-panel-custom.ts:94
    of frontend 20260128.6) reads panel.config._panel_custom. A flat config
    made that undefined -> "can't access property html_url" (the live
    exhibit). The registration must nest the element config."""

    def test_config_nests_under_panel_custom(self):
        cfg = panel_custom_config()
        self.assertIn("_panel_custom", cfg)
        inner = cfg["_panel_custom"]
        self.assertEqual(inner["name"], "ai-terminal-panel")

    def test_config_module_url_serves_panel_js(self):
        url = panel_custom_config()["_panel_custom"]["module_url"]
        self.assertEqual(url, "/api/ai_terminal/www/panel.js")

    def test_config_trust_external_same_origin(self):
        # the module is served by HA itself (/api/...), so the trust prompt
        # is bypassed legitimately -- but the key must exist for the loader
        self.assertTrue(panel_custom_config()["_panel_custom"]["trust_external"])

    def test_get_url_branch_is_module_only(self):
        # the loader's getUrl: html_url -> module(+js) -> js_url. We provide
        # ONLY module_url -> the {type: "module"} branch must fire.
        inner = panel_custom_config()["_panel_custom"]
        self.assertNotIn("html_url", inner)
        self.assertNotIn("js_url", inner)
        self.assertIn("module_url", inner)

    def test_static_paths_shape(self):
        self.assertEqual(static_paths(), [("/api/ai_terminal/www", "www")])

    def test_field_panel_contract(self):
        """The field map (2026-10-08): its own element + module, the same
        nested _panel_custom contract."""
        from ai_terminal.panel_config import (FIELD_PANEL_URL_PATH,
                                              field_panel_config)
        inner = field_panel_config()["_panel_custom"]
        self.assertEqual(inner["name"], "membrane-field-panel")
        self.assertEqual(inner["module_url"], "/api/ai_terminal/www/field.js")
        self.assertTrue(inner["trust_external"])
        self.assertNotIn("html_url", inner)
        self.assertEqual(FIELD_PANEL_URL_PATH, "membrane-field")


    def test_home_panel_contract(self):
        """Membrane home (2026-10-10): the 3D field + the chat overlaid."""
        from ai_terminal.panel_config import HOME_PANEL_URL_PATH, home_panel_config
        inner = home_panel_config()["_panel_custom"]
        self.assertEqual(inner["name"], "membrane-home-panel")
        self.assertEqual(inner["module_url"], "/api/ai_terminal/www/home.js")
        self.assertTrue(inner["trust_external"])
        self.assertEqual(HOME_PANEL_URL_PATH, "membrane")


class TestAssetMimeContract(unittest.TestCase):
    """The nosniff lesson: HA's static serving guessed text/plain for
    .js/.css on the box and the browser BLOCKED the response. The asset
    view pins content types; the mapping is a contract, not a detail."""

    def test_asset_types_pinned(self):
        from ai_terminal.views import AssetView
        for ext, ctype in ((".js", "application/javascript"),
                           (".css", "text/css")):
            self.assertEqual(AssetView._TYPES[ext], ctype)

    def test_asset_view_traversal_guard(self):
        import asyncio
        from ai_terminal.views import AssetView

        # HA's view wrapper passes match groups as KWARGS
        # (handler(request, **match_info)) -- call it the same way.
        class _Hass:                     # the executor hop, inline
            async def async_add_executor_job(self, fn, *a):
                return fn(*a)

        class _Req:
            app = {"hass": _Hass()}

        out = asyncio.run(AssetView().get(_Req(), asset="../../etc/passwd"))
        self.assertEqual(out.status, 404)
        out = asyncio.run(AssetView().get(_Req(), asset="panel.js"))
        self.assertEqual(out.status, 200)


class TestChatOnlySurface(unittest.TestCase):
    """The v1 PTY lane is gone (membrane must not run on agdroid -- RAM):
    no terminal/ticket/seat modules, no xterm assets, no WS view."""

    PKG = ROOT / "custom_components" / "ai_terminal"

    def test_no_terminal_modules(self):
        for name in ("terminal.py", "tickets.py", "seat.py"):
            self.assertFalse((self.PKG / name).exists(), name)
        self.assertEqual(sorted(p.name for p in (self.PKG / "www").iterdir()),
                         ["field.js", "field3d.js", "home.js", "panel.js", "stranger.js", "three.min.js"])

    def test_panel_speaks_serve_only(self):
        js = (self.PKG / "www" / "panel.js").read_text()
        self.assertNotIn("ttydws", js)
        self.assertNotIn("ai_terminal/ticket", js)
        self.assertIn("/api/ai_terminal/serve", js)
        # turn completion reads the brain's own status, not a stability guess
        self.assertIn("/session/status", js)
        # sessions follow the user (server-side list), not this browser: the
        # only thing it reads from browser storage is membrane home's landing
        # flag (2026-10-10)
        import re as _re
        self.assertEqual(set(_re.findall(r'localStorage\.\w+\("([^"]+)"', js)), {"membrane.home.left"})
        # the proxy's speaker line is hidden from the transcript
        self.assertIn('SPEAKER_PREFIX = "[HA user: "', js)
        # the field map's "ask the brain" hands its question over the URL
        self.assertIn('get("ask")', js)

    def test_conf_view_reads_component(self):
        """The live exhibit: 'HomeAssistantRequest' object has no
        attribute 'hass' -- HA keeps the instance at request.app[KEY_HASS]
        (the panel then showed 'brain lane off')."""
        import asyncio
        from ai_terminal.serve_proxy import ConfView

        class _Hass:
            data = {"ai_terminal": {"serve_url": "http://b:4096",
                                    "serve_pass": "pw"}}

        class _Req:
            app = {"hass": _Hass()}

        out = asyncio.run(ConfView().get(_Req()))
        self.assertEqual(out[1], {"serve_url": "http://b:4096",
                                  "serve_ok": True})

    def test_proxy_handlers_take_match_kwargs(self):
        """HA's view wrapper calls handler(request, **match_info): every
        proxy method must accept the URL's `path` group."""
        import inspect
        from ai_terminal.serve_proxy import ServeProxyView
        for m in ("get", "post", "patch", "delete"):
            sig = inspect.signature(getattr(ServeProxyView, m))
            self.assertIn("path", sig.parameters, m)


if __name__ == "__main__":
    unittest.main()
