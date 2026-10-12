"""The setup form: run the seat inside Home Assistant (it thinks with an
Ollama server -- models picked from its list) or connect to a seat that runs
elsewhere (the boat's way: a separate brain machine)."""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from .const import (CONF_BACKEND, CONF_EMBED_MODEL, CONF_MODE, CONF_MODEL, CONF_SERVE_PASS,
                    CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_BACKEND, DEFAULT_LOOPS,
                    DEFAULT_SERVE_USER, DOMAIN, MODE_CONNECT, MODE_INSIDE, OPT_LANDING, OPT_LOOPS,
                    OPT_STT_URL)

EMBED_MARKERS = ("embed", "minilm", "bge", "e5-", "gte")


async def ollama_models(hass, url: str) -> list[str]:
    s = async_get_clientsession(hass)
    async with s.get(url.rstrip("/") + "/api/tags", timeout=aiohttp.ClientTimeout(total=10)) as r:
        r.raise_for_status()
        d = await r.json()
    return sorted(str(m.get("name") or "") for m in d.get("models") or [] if m.get("name"))


def split_models(names: list[str]) -> tuple[list[str], list[str]]:
    emb = [n for n in names if any(k in n.lower() for k in EMBED_MARKERS)]
    return [n for n in names if n not in emb], emb


class MembraneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self):
        self._backend = DEFAULT_BACKEND
        self._models: list[str] = []

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)          # one seat per Home Assistant
        self._abort_if_unique_id_configured()
        return self.async_show_menu(step_id="user", menu_options=[MODE_INSIDE, MODE_CONNECT])

    async def async_step_inside(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._backend = user_input[CONF_BACKEND].rstrip("/")
            try:
                self._models = await ollama_models(self.hass, self._backend)
            except Exception:                            # noqa: BLE001
                errors["base"] = "no_ollama"
            else:
                if not self._models:
                    errors["base"] = "no_models"
                else:
                    return await self.async_step_models()
        return self.async_show_form(step_id="inside", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_BACKEND, default=self._backend): str}))

    async def async_step_models(self, user_input=None):
        chat, emb = split_models(self._models)
        if user_input is not None:
            return self.async_create_entry(title="Membrane", data={
                CONF_MODE: MODE_INSIDE, CONF_BACKEND: self._backend,
                CONF_MODEL: user_input[CONF_MODEL], CONF_EMBED_MODEL: user_input[CONF_EMBED_MODEL]})
        pick = lambda opts: SelectSelector(SelectSelectorConfig(options=opts or self._models,
                                                                mode=SelectSelectorMode.DROPDOWN))
        return self.async_show_form(step_id="models", data_schema=vol.Schema({
            vol.Required(CONF_MODEL, default=(chat or self._models)[0]): pick(chat),
            vol.Required(CONF_EMBED_MODEL, default=(emb or self._models)[0]): pick(emb),
        }), description_placeholders={"backend": self._backend})

    async def async_step_connect(self, user_input=None):
        errors = {}
        if user_input is not None:
            s = async_get_clientsession(self.hass)
            try:
                async with s.get(user_input[CONF_SERVE_URL].rstrip("/") + "/global/health",
                                 auth=aiohttp.BasicAuth(user_input[CONF_SERVE_USER], user_input[CONF_SERVE_PASS]),
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    ok = r.status == 200
            except Exception:                            # noqa: BLE001
                ok = False
            if ok:
                return self.async_create_entry(title="Membrane", data={CONF_MODE: MODE_CONNECT, **user_input})
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="connect", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_SERVE_URL): str,
            vol.Required(CONF_SERVE_USER, default=DEFAULT_SERVE_USER): str,
            vol.Required(CONF_SERVE_PASS): str}))

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return MembraneOptionsFlow()


class MembraneOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(OPT_LANDING, default=o.get(OPT_LANDING, True)): bool,
            vol.Optional(OPT_STT_URL, default=o.get(OPT_STT_URL, "")): str,
            vol.Required(OPT_LOOPS, default=o.get(OPT_LOOPS, DEFAULT_LOOPS)): vol.All(int, vol.Range(min=1, max=3)),
        }))
