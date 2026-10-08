"""Membrane AI: hass's AI assistant lane over the brain's native serve.

The conversation entity POSTs each Assist turn to the membrane serve's
SYNC message endpoint (/session/{sid}/message) -- one serve session per
hass conversation, so the brain's field memory carries the thread. The
full turn loop (gate, routing, banner, capture) is the SERVER's: the
assistant here is a client, not a runner.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (CONF_NAME, CONF_SERVE_PASS, CONF_SERVE_URL,
                    CONF_SERVE_USER, DEFAULT_NAME, DEFAULT_SERVE_PASS,
                    DEFAULT_SERVE_URL, DEFAULT_SERVE_USER, DOMAIN)

_LOGGER = logging.getLogger(__name__)
# the field metrics (2026-10-08): sensors + the paused flag, polled by
# coordinator.FieldCoordinator off the serve's relayed /membrane/state
PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config) -> bool:
    """Optional yaml (`membrane_ai: {serve_url, serve_user, serve_pass}`):
    imported into the config entry, so the agent needs no UI flow."""
    conf = config.get(DOMAIN)
    if conf and conf.get(CONF_SERVE_URL) and conf.get(CONF_SERVE_PASS):
        from homeassistant.config_entries import SOURCE_IMPORT
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(conf)))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "name": entry.data.get(CONF_NAME) or DEFAULT_NAME,
        "serve_url": entry.data.get(CONF_SERVE_URL) or DEFAULT_SERVE_URL,
        "serve_user": entry.data.get(CONF_SERVE_USER) or DEFAULT_SERVE_USER,
        "serve_pass": entry.data.get(CONF_SERVE_PASS) or DEFAULT_SERVE_PASS,
        "sessions": {},
    }
    from .coordinator import FieldCoordinator
    comp = hass.data[DOMAIN][entry.entry_id]
    comp["coordinator"] = FieldCoordinator(hass, entry, comp)
    # never block the Assist agent on the brain: the sensors start
    # unavailable and fill on the next successful poll
    await comp["coordinator"].async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down the entities + close the per-entry serve session map."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    comp = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if comp and comp.get("sessions"):
        # the serve sessions stay on the brain (its own store); just forget
        # the mapping -- a fresh entry re-resolves lazily.
        comp["sessions"] = {}
    return True
