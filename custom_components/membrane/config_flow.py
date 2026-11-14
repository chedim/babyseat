"""The setup form (connect-only, #1143): the address of a seat the user runs, and a token for it. The token is minted on the seat
(`cliffvm token mint`, docs/home-assistant.md) and kept in the entry's `serve_pass`. A token has a life: when the seat refuses it
(a 401) Home Assistant starts the reauth flow and the user pastes a new one."""
from __future__ import annotations

from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SERVE_PASS, CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_SERVE_USER, DOMAIN
from .seat_client import SeatAuthError, SeatClient, SeatError


def clean_url(url: str) -> str:
    """The address as typed, without trailing slashes; "" when it is not an http(s) URL with a host."""
    u = str(url or "").strip().rstrip("/")
    p = urlsplit(u)
    return u if p.scheme in ("http", "https") and p.netloc else ""


class MembraneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 3               # connect-only: the entry is {serve_url, serve_user, serve_pass} (async_migrate_entry)

    async def _check(self, url: str, token: str, user: str = DEFAULT_SERVE_USER) -> str:
        """"" when the seat answers its health with this token, else the error key."""
        try:
            await SeatClient(async_get_clientsession(self.hass), url, token, user).health()
        except SeatAuthError:
            return "invalid_auth"
        except SeatError:
            return "cannot_connect"
        return ""

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)          # one entry per Home Assistant
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            url = clean_url(user_input[CONF_SERVE_URL])
            if not url:
                errors[CONF_SERVE_URL] = "invalid_url"
            else:
                err = await self._check(url, user_input[CONF_SERVE_PASS])
                if not err:
                    return self.async_create_entry(title="Membrane", data={
                        CONF_SERVE_URL: url, CONF_SERVE_USER: DEFAULT_SERVE_USER, CONF_SERVE_PASS: user_input[CONF_SERVE_PASS]})
                errors["base"] = err
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_SERVE_URL, default=(user_input or {}).get(CONF_SERVE_URL, "")): str,
            vol.Required(CONF_SERVE_PASS): str}))

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """The seat refused the token (it expired, or the seat changed its key), or the entry has no seat yet (it ran the seat inside
        Home Assistant): a new token, and the address too when the entry has none."""
        entry = self._get_reauth_entry()
        old_url = str(entry.data.get(CONF_SERVE_URL) or "")
        errors = {}
        if user_input is not None:
            url = clean_url(user_input.get(CONF_SERVE_URL) or old_url)
            if not url:
                errors[CONF_SERVE_URL] = "invalid_url"
            else:
                err = await self._check(url, user_input[CONF_SERVE_PASS], entry.data.get(CONF_SERVE_USER) or DEFAULT_SERVE_USER)
                if not err:
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_SERVE_URL: url, CONF_SERVE_PASS: user_input[CONF_SERVE_PASS]})
                errors["base"] = err
        fields = {}
        if not old_url:
            fields[vol.Required(CONF_SERVE_URL)] = str
        fields[vol.Required(CONF_SERVE_PASS)] = str
        return self.async_show_form(step_id="reauth_confirm", errors=errors, data_schema=vol.Schema(fields),
                                    description_placeholders={"url": old_url or "(none yet)"})
