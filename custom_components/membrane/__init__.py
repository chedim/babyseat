"""Membrane for Home Assistant (2026-10-12, the operator: "it should install
into HA and run inside it :) and it should change the UI the same way we did on
Agatha").

- The seat: HA's own child process (supervisor.py), or a seat elsewhere
  (connect mode -- the boat's way).
- The UI, as on the boat: membrane home (the 3D field, the chat on glass, voice)
  is everyone's landing page; HA's sidebar folds away on it and its ☰ menu
  leads to HA's own pages; the chat and the field map are admin panels. The
  files are the installed package's own (views.py) -- the same UI as
  `membrane standalone`.
- The relay (relay.py): every call goes through HA's auth; the seat's pair stays
  server-side; each person sees their own chats; each message carries
  "[HA user: <name>]".
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CoreState, HomeAssistant

from .const import (CONF_BACKEND, CONF_EMBED_MODEL, CONF_MODE, CONF_MODEL, CONF_SERVE_PASS,
                    CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_LOOPS, DEFAULT_SERVE_USER, DOMAIN,
                    MODE_INSIDE, OPT_LANDING, OPT_LOOPS, OPT_STT_URL)

_LOGGER = logging.getLogger(__name__)

ENTRY = "/api/membrane/www/entry.js?m="
PANELS = (  # url path, sidebar title, icon, element, module, admin only
    ("membrane", "Membrane", "mdi:cube-scan", "membrane-home-panel", "home.js", False),
    ("membrane-chat", "Membrane chat", "mdi:brain", "ai-terminal-panel", "panel.js", True),
    ("membrane-field", "Membrane field", "mdi:graph-outline", "membrane-field-panel", "field.js", True),
)


def _panel_config(element: str, module: str) -> dict:
    # HA's custom panel contract: config._panel_custom {name, module_url} (ha-panel-custom.ts)
    return {"_panel_custom": {"name": element, "module_url": ENTRY + module, "trust_external": True}}


async def async_setup(hass: HomeAssistant, config) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


def _register_views(hass: HomeAssistant, comp: dict) -> None:
    if comp.get("views"):
        return                                   # HA can't unregister a view: once per HA run
    from .relay import ServeProxyView
    from .stt import SttView
    from .views import AssetView, ConfView, NameView
    for v in (AssetView(), NameView(), ConfView(), ServeProxyView(), SttView()):
        hass.http.register_view(v)
    comp["views"] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.components import frontend as fe
    comp = hass.data.setdefault(DOMAIN, {})
    data, opts = entry.data, entry.options
    comp["stt_url"] = str(opts.get(OPT_STT_URL) or "")
    comp.setdefault("name", "Membrane")
    _register_views(hass, comp)

    for path, title, icon, element, module, admin in PANELS:
        fe.async_register_built_in_panel(hass, "custom", sidebar_title=title, sidebar_icon=icon,
                                         frontend_url_path=path, config=_panel_config(element, module),
                                         require_admin=admin, update=True)
    # THE LANDING PAGE (the boat's way): panel.js on every HA page sends the bare
    # landing (/, /lovelace, /home) to membrane home, unless the person left it
    # through its ☰ menu (panel.js LANDING_RE, membrane.home.left)
    if opts.get(OPT_LANDING, True):
        fe.add_extra_js_url(hass, ENTRY + "panel.js")
        comp["landing"] = True

    if data.get(CONF_MODE) == MODE_INSIDE:
        from .supervisor import SeatSupervisor
        comp["inside"] = True
        sup = SeatSupervisor(hass, comp, data[CONF_BACKEND], data.get(CONF_MODEL, ""),
                             data.get(CONF_EMBED_MODEL, ""), int(opts.get(OPT_LOOPS, DEFAULT_LOOPS)))
        comp["supervisor"] = sup

        async def _start(_event=None):
            await sup.start()
        if hass.state is CoreState.running:
            await _start()
        else:
            entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _start))

        async def _stop(_event):
            await sup.stop()
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _stop))
    else:
        comp["inside"] = False
        comp["serve_url"] = str(data.get(CONF_SERVE_URL) or "").rstrip("/")
        comp["serve_user"] = str(data.get(CONF_SERVE_USER) or DEFAULT_SERVE_USER)
        comp["serve_pass"] = str(data.get(CONF_SERVE_PASS) or "")
    entry.async_on_unload(entry.add_update_listener(_reload))
    _LOGGER.info("membrane: set up (%s)", "the seat inside HA" if comp["inside"] else comp.get("serve_url"))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.components import frontend as fe
    comp = hass.data.get(DOMAIN) or {}
    sup = comp.pop("supervisor", None)
    if sup is not None:
        await sup.stop()
    for path, *_ in PANELS:
        fe.async_remove_panel(hass, path)
    if comp.pop("landing", False):
        fe.remove_extra_js_url(hass, ENTRY + "panel.js")
    for k in ("serve_url", "serve_pass"):
        comp.pop(k, None)
    sess = comp.pop("http_session", None)
    if sess is not None and not sess.closed:
        await sess.close()
    return True
