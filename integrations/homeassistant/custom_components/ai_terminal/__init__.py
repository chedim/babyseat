"""AI Terminal: a web chat client for the brain, gated by Home Assistant auth.

The panel is a chat client for membrane's NATIVE serve surface (the
opencode-compatible REST/SSE of membrane/server.py) running on the brain
phone -- proxied through HA's own server (ServeProxyView) so the serve
credential stays server-side and the lane works from LAN and outside
alike. The turn loop, the memory field, the routing are the brain's own:
the dashboard is a client, not a runner. (The v1 tmux/opencode PTY lane
is gone: membrane must not run on agdroid -- RAM.)

Auth model: the panel element runs INSIDE HA's frontend where `this.hass`
holds the auth; every call is hass.fetchWithAuth onto /api/ai_terminal/*.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import (CONF_SERVE_PASS, CONF_SERVE_URL, CONF_SERVE_USER,
                    DEFAULT_SERVE_PASS, DEFAULT_SERVE_URL,
                    DEFAULT_SERVE_USER, DOMAIN)
from .panel_config import panel_custom_config

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config) -> bool:
    conf = config.get(DOMAIN) or {}
    component = hass.data.setdefault(DOMAIN, {})
    # empty serve_pass keeps the lane off (the panel says so)
    component["serve_url"] = str(conf.get(CONF_SERVE_URL,
                                          DEFAULT_SERVE_URL) or "")
    component["serve_user"] = str(conf.get(CONF_SERVE_USER,
                                           DEFAULT_SERVE_USER) or "")
    component["serve_pass"] = str(conf.get(CONF_SERVE_PASS,
                                           DEFAULT_SERVE_PASS) or "")

    # the views ride HA's own router (auth = the HA session)
    from .views import AssetView
    from .serve_proxy import ServeProxyView, ConfView
    hass.http.register_view(AssetView())
    hass.http.register_view(ServeProxyView())
    hass.http.register_view(ConfView())
    from .strangers import StrangerView
    hass.http.register_view(StrangerView())

    # THE PANEL (programmatic, this HA version has no yaml panels): a
    # panel_custom element running INSIDE HA's frontend.
    from homeassistant.components import frontend as fe
    fe.add_extra_js_url(hass, f"/api/{DOMAIN}/www/panel.js")
    fe.async_register_built_in_panel(
        hass, "custom", sidebar_title="AI Dashboard",
        sidebar_icon="mdi:brain", frontend_url_path="ai-terminal",
        config=panel_custom_config(DOMAIN),
        require_admin=True, update=True)
    # THE FIELD MAP (2026-10-08): the brain's field as a 2D map
    from .panel_config import FIELD_PANEL_URL_PATH, field_panel_config
    fe.async_register_built_in_panel(
        hass, "custom", sidebar_title="Membrane Field",
        sidebar_icon="mdi:graph-outline", frontend_url_path=FIELD_PANEL_URL_PATH,
        config=field_panel_config(DOMAIN), require_admin=True, update=True)

    # MEMBRANE HOME (2026-10-10): the field in 3D following you + the chat
    # overlaid; HA's UI one button away
    from .panel_config import HOME_PANEL_URL_PATH, home_panel_config
    fe.async_register_built_in_panel(
        hass, "custom", sidebar_title="Membrane",
        sidebar_icon="mdi:cube-scan", frontend_url_path=HOME_PANEL_URL_PATH,
        config=home_panel_config(DOMAIN), require_admin=False, update=True)   # everyone's UI (option 2)

    async def _close_session(_event):
        sess = component.get("http_session")
        if sess is not None and not sess.closed:
            await sess.close()
    hass.bus.async_listen_once("homeassistant_stop", _close_session)

    _LOGGER.info("ai_terminal: panel + views registered (url_path "
                 "ai-terminal; serve=%s)", component["serve_url"])
    return True
