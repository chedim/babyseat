"""Config flow: one form pointing at the brain's native serve."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (CONF_SERVE_PASS, CONF_SERVE_URL, CONF_SERVE_USER,
                    DEFAULT_NAME, DEFAULT_SERVE_URL, DEFAULT_SERVE_USER,
                    DOMAIN)

_LOGGER = logging.getLogger(__name__)


class MembraneAIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """The brain serve connection flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            auth = aiohttp.BasicAuth(user_input[CONF_SERVE_USER],
                                     user_input[CONF_SERVE_PASS])
            url = user_input[CONF_SERVE_URL].rstrip("/") + "/global/health"
            try:
                async with session.get(
                        url, auth=auth,
                        timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status != 200:
                        errors["base"] = "cannot_connect"
                    else:
                        await self.async_set_unique_id(
                            user_input[CONF_SERVE_URL])
                        self._abort_if_unique_id_configured()
                        return self.async_create_entry(
                            title=user_input.get(CONF_NAME) or DEFAULT_NAME,
                            data=user_input)
            except Exception:                       # noqa: BLE001
                errors["base"] = "cannot_connect"
        schema = vol.Schema({
            vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
            vol.Required(CONF_SERVE_URL, default=DEFAULT_SERVE_URL): str,
            vol.Required(CONF_SERVE_USER, default=DEFAULT_SERVE_USER): str,
            vol.Required(CONF_SERVE_PASS): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema,
                                    errors=errors)

    async def async_step_import(self, import_data):
        """configuration.yaml `membrane_ai:` -> the entry (created once,
        then kept in sync with the yaml; no reachability gate -- the brain
        may be down at hass boot and the entity reports that per turn)."""
        data = {
            CONF_NAME: import_data.get(CONF_NAME) or DEFAULT_NAME,
            CONF_SERVE_URL: import_data[CONF_SERVE_URL],
            CONF_SERVE_USER: import_data.get(CONF_SERVE_USER)
            or DEFAULT_SERVE_USER,
            CONF_SERVE_PASS: import_data[CONF_SERVE_PASS],
        }
        await self.async_set_unique_id(data[CONF_SERVE_URL])
        self._abort_if_unique_id_configured(updates=data)
        return self.async_create_entry(title=data[CONF_NAME], data=data)
