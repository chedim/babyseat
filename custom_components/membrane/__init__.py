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
- The zoo (zoo.py, #283): a list of seats in the options, one active for the
  whole house (select.membrane_seat, membrane home's 🦠 cards); a switch is
  applied live, no reload.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import CoreState, HomeAssistant

from . import zoo
from .const import (CONF_BACKEND, CONF_EMBED_MODEL, CONF_MODE, CONF_MODEL, DEFAULT_LOOPS, DOMAIN,
                    MODE_INSIDE, OPT_HA_ACCESS, OPT_HA_BASE_URL, OPT_LANDING, OPT_LOOPS, OPT_STT_URL)

_LOGGER = logging.getLogger(__name__)

ENTRY = "/api/membrane/www/entry.js?m="
# the seat as Assist's conversation agent + its field's readings (from the boat's membrane_ai)
# + the house's active seat (#283: select.membrane_seat)
PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT]
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
    from .views import AssetView, ConfView, NameView, ZooActiveView, ZooView
    for v in (AssetView(), NameView(), ConfView(), ServeProxyView(), SttView(), ZooView(), ZooActiveView()):
        hass.http.register_view(v)
    comp["views"] = True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 -> 1.2 (#283): the one seat becomes a one-seat list, connecting exactly
    as before (a minor bump: an older integration still loads the entry)."""
    if entry.version == 1 and entry.minor_version < 2:
        hass.config_entries.async_update_entry(entry, options=zoo.migrate(dict(entry.data), dict(entry.options)),
                                               minor_version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.components import frontend as fe
    comp = hass.data.setdefault(DOMAIN, {})
    data = entry.data
    opts = zoo.migrate(dict(data), dict(entry.options))
    if opts != dict(entry.options):                   # a fresh entry: its first seat written down
        hass.config_entries.async_update_entry(entry, options=opts)
    comp["stt_default"] = str(opts.get(OPT_STT_URL) or "")
    comp["stt_url"] = comp["stt_default"]
    comp.setdefault("name", "Bob")
    # THE ZOO (#283): the seats, the active one; every reader of comp["serve_*"]
    # follows the active seat (zoo.apply), a switch moves them live (zoo.switch)
    comp["entry"] = entry
    comp[zoo.SEATS] = list(opts[zoo.SEATS])
    comp["applied_opts"] = zoo.settings_of(opts)
    comp["on_switch"] = []
    comp["inside_slot"] = slot = zoo.SeatSlot(comp)   # HA's own child writes here (supervisor.py)
    zoo.apply(comp, zoo.active_of(comp[zoo.SEATS], opts.get(zoo.ACTIVE)))
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
        sup = SeatSupervisor(hass, slot, data[CONF_BACKEND],
                             opts.get(CONF_MODEL) or data.get(CONF_MODEL, ""),          # the options' pick wins
                             opts.get(CONF_EMBED_MODEL) or data.get(CONF_EMBED_MODEL, ""),
                             int(opts.get(OPT_LOOPS, DEFAULT_LOOPS)))
        comp["supervisor"] = sup
        if opts.get(OPT_HA_ACCESS, True):
            # THE SEAT MAY USE HOME ASSISTANT (ha_access.py): a hidden user's token to act,
            # the state changes of the entities it mirrors pushed to sense
            from . import ha_access as HA
            async def _ha_env():
                return {"HASS_URL": HA.local_url(hass), "HASS_TOKEN": await HA.mint_token(hass, entry)}
            sup.extra_env = _ha_env
            push = HA.StatePush(hass, slot)               # HA's child mirrors HA, active or not
            push.start()
            comp["state_push"] = push

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
        # the active seat's url, user and token (#253: a seat on another machine
        # refuses HA's writes without it; Basic's password) are comp's already
        # (zoo.apply above); a seat elsewhere is up as configured
        if opts.get(OPT_HA_ACCESS, True):
            # DETACHED HOME ASSISTANT ACCESS (ha_access.py): the same StatePush as
            # inside mode (its target is comp["serve_url"] either way), and the
            # token POSTed to the seat at every HA start -- it lands in the seat's
            # ~/.config/membrane/hass.env and is read on its next turn, so an HA
            # restart reaches the seat without a seat restart. The ha-start /
            # ha-stop notices ride the same door (restarts.py): inside mode has
            # no such notice because the child announces its own boot with the
            # downtime; a detached seat would otherwise read an HA restart as the
            # world going quiet.
            from . import ha_access as HA
            comp["ha_base_url"] = str(opts.get(OPT_HA_BASE_URL) or "")
            push = HA.StatePush(hass, comp)
            push.start()
            comp["state_push"] = push

            # NOTE (learned in the 2026-10-12 docker rehearsal): a bus listener
            # that is a plain sync callable runs in HA's executor thread --
            # a lambda there called hass.async_create_background_task, an
            # event-loop-only method, and died with "loop is not the running
            # loop"; the ha-start coroutine was never awaited. ha-start is an
            # async listener on the loop. ha-stop must survive shutdown, when
            # background tasks are the first things cancelled -- so it posts
            # by itself, in whatever thread it lands in (seat_event_sync).
            async def _announced(_event=None):
                await HA.push_token(hass, entry, comp)
                if await HA.seat_event(comp, "ha-start"):
                    _LOGGER.info("membrane: the detached seat heard Home Assistant start")

            if hass.state is CoreState.running:
                hass.async_create_background_task(_announced(), "membrane ha-start")
            # #283: a switched-to seat gets Home Assistant access at once
            comp["on_switch"].append(lambda: hass.async_create_background_task(
                HA.push_token(hass, entry, comp), "membrane ha token"))
            entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _announced))
            entry.async_on_unload(hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STOP, lambda _e: HA.seat_event_sync(comp, "ha-stop")))
    from .coordinator import FieldCoordinator
    comp.setdefault("sessions", {})
    comp["coordinator"] = FieldCoordinator(hass, entry, comp)
    hass.async_create_task(comp["coordinator"].async_refresh())     # never wait on the seat at setup
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    _LOGGER.info("membrane: set up (%s)", "the seat inside HA" if comp["inside"] else comp.get("serve_url"))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    comp = hass.data.get(DOMAIN) or {}
    if comp.get("applied_opts") == zoo.settings_of(entry.options):
        return                                       # #283: only the active seat moved -- applied live
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from homeassistant.components import frontend as fe
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    comp = hass.data.get(DOMAIN) or {}
    comp.pop("coordinator", None)
    comp["sessions"] = {}
    sup = comp.pop("supervisor", None)
    if sup is not None:
        await sup.stop()
    push = comp.pop("state_push", None)
    if push is not None:
        push.stop()
    for path, *_ in PANELS:
        fe.async_remove_panel(hass, path)
    if comp.pop("landing", False):
        fe.remove_extra_js_url(hass, ENTRY + "panel.js")
    for k in ("serve_url", "serve_pass", "serve_ready", "inside_slot", "entry", "on_switch", "applied_opts",
              "active_seat", "seat_name", zoo.SEATS):
        comp.pop(k, None)
    sess = comp.pop("http_session", None)
    if sess is not None and not sess.closed:
        await sess.close()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration removed: the seat's hidden Home Assistant user goes with it."""
    from . import ha_access as HA
    await HA.remove_user(hass, entry)
