"""The setup (#1143). The first step is a menu (operator 2026-11-15): "Run a seat inside Home Assistant" (the default: the component downloads the
seat of its own release and runs it, seat_runner.py; the person picks the provider, Anthropic, an OpenAI-compatible endpoint or Ollama, and its key, model
or URL; Home Assistant's own Ollama integration prefills the Ollama form; an options flow changes them later) or "Connect to a seat I run": the address
of a seat the user runs, and a token for it. The token is minted on the seat
(`cliffvm token mint`, docs/home-assistant.md) and kept in the entry's `serve_pass`. A token has a life: when the seat refuses it
(a 401) Home Assistant starts the reauth flow and the user pastes a new one. The babyseat add-on announces its seat through the Supervisor
(`async_step_hassio`): the address and the token come from it, and a later announcement keeps the entry's token current."""
from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig,
                                            TextSelectorType)

from .const import (CONF_ANTHROPIC_KEY, CONF_API_KEY, CONF_BASE_URL, CONF_MODE, CONF_MODEL, CONF_OLLAMA_URL, CONF_PROVIDER, CONF_SEAT_NAME,
                    CONF_SERVE_PASS, CONF_SERVE_PORT, CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_PORT, DEFAULT_SERVE_USER, DOCS_URL, DOMAIN, MODE_INSIDE,
                    SEAT_KEYS)
from .seat_client import SeatAuthError, SeatClient, SeatError
from .seat_options import DEFAULT_ANTHROPIC_MODEL, PROVIDERS, SeatOptionsError, seat_facts
from .seat_runner import UnsupportedPlatform, free_port, release_target

if TYPE_CHECKING:                                 # only a type: the step reads .config and .name
    from homeassistant.helpers.service_info.hassio import HassioServiceInfo


def clean_url(url: str) -> str:
    """The address as typed, without trailing slashes; "" when it is not an http(s) URL with a host."""
    u = str(url or "").strip().rstrip("/")
    p = urlsplit(u)
    return u if p.scheme in ("http", "https") and p.netloc else ""


def ha_ollama(hass) -> tuple[str, str] | None:
    """Home Assistant's own Ollama integration, when it has one: (its server URL, the model its conversation agent uses; "" when none is
    set). Read-only, no network call: a hint for the seat's own setup (the 0.7.53 component's, kept). An entry without a URL is skipped."""
    for e in hass.config_entries.async_entries("ollama"):
        url = str((e.data or {}).get("url") or "").strip()
        if not url:
            continue
        model = str((e.data or {}).get("model") or (e.options or {}).get("model") or "")
        for sub in (getattr(e, "subentries", None) or {}).values():
            if getattr(sub, "subentry_type", "") == "conversation" and (getattr(sub, "data", None) or {}).get("model"):
                model = str(sub.data["model"])
                break
        return url.rstrip("/"), model
    return None


def ollama_hint(hass, step_id: str, placeholders: dict) -> tuple[str, dict]:
    """The form with the Ollama hint (its own step id `<step>_ollama` and text) when Home Assistant has an Ollama server, else the plain one."""
    found = ha_ollama(hass)
    if found is None:
        return step_id, placeholders
    return f"{step_id}_ollama", {**placeholders, "ollama_url": found[0], "ollama_model": found[1] or "(none set)"}


def seat_settings(entry) -> dict:
    """The seat settings of an inside entry: the options flow's (they replace the setup's whole) or the setup's; only the keys that hold a value."""
    src = entry.options if (entry.options or {}).get(CONF_PROVIDER) else entry.data
    return {k: src[k] for k in SEAT_KEYS if src.get(k)}


PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
SECRET_KEYS = {"anthropic": CONF_ANTHROPIC_KEY, "openai": CONF_API_KEY}


def _req(key, default):
    return vol.Required(key, default=default) if default else vol.Required(key)


def _opt(key, default):
    return vol.Optional(key, default=default) if default else vol.Optional(key)


def provider_schema(hass, provider: str, cur: dict, key_required: bool) -> vol.Schema:
    """The provider's fields. `cur`: the values to show (a key is never shown back: its field stays empty). Ollama: Home Assistant's own Ollama server and model."""
    if provider == "ollama":
        found = ha_ollama(hass) or ("", "")
        fields = {_req(CONF_OLLAMA_URL, cur.get(CONF_OLLAMA_URL) or found[0]): URL, _req(CONF_MODEL, cur.get(CONF_MODEL) or found[1]): str}
    elif provider == "openai":
        fields = {_req(CONF_BASE_URL, cur.get(CONF_BASE_URL)): URL, _req(CONF_MODEL, cur.get(CONF_MODEL)): str, vol.Optional(CONF_API_KEY): PASSWORD}
    else:
        fields = {(vol.Required if key_required else vol.Optional)(CONF_ANTHROPIC_KEY): PASSWORD,
                  _req(CONF_MODEL, cur.get(CONF_MODEL) or DEFAULT_ANTHROPIC_MODEL): str}
    fields[_opt(CONF_SEAT_NAME, cur.get(CONF_SEAT_NAME))] = str
    return vol.Schema(fields)


def provider_settings(provider: str, user_input: dict, keep: dict) -> tuple[dict, dict]:
    """-> (the settings to store, the form's errors). An empty key keeps the one in `keep` (the options flow). Checked without the network:
    seat_options' own rules (a key, an http(s) URL, a model)."""
    out = {CONF_PROVIDER: provider}
    for k, v in (user_input or {}).items():
        v = str(v or "").strip()
        if k in (CONF_OLLAMA_URL, CONF_BASE_URL):
            v = v.rstrip("/")
        if v:
            out[k] = v
    secret = SECRET_KEYS.get(provider)
    if secret and not out.get(secret) and keep.get(CONF_PROVIDER) == provider and keep.get(secret):
        out[secret] = keep[secret]
    try:
        seat_facts(out)
    except SeatOptionsError as e:
        return out, {e.field: "invalid_url" if e.field in (CONF_OLLAMA_URL, CONF_BASE_URL) else "required"}
    return {k: out[k] for k in SEAT_KEYS if out.get(k)}, {}


def provider_picker(default: str) -> vol.Schema:
    return vol.Schema({vol.Required(CONF_PROVIDER, default=default): SelectSelector(SelectSelectorConfig(
        options=list(PROVIDERS), translation_key="provider", mode=SelectSelectorMode.LIST))})


class _ProviderSteps:
    """The provider's form, shared by the setup and the options flow: `_seat_done(settings)` ends it; `_keep` is what an empty key keeps."""
    _keep: dict = {}
    _key_required = True

    async def _provider_step(self, provider: str, user_input):
        errors = {}
        if user_input is not None:
            settings, errors = provider_settings(provider, user_input, self._keep)
            if not errors:
                return await self._seat_done(settings)
        src = user_input if user_input is not None else (self._keep if self._keep.get(CONF_PROVIDER) == provider else {})
        shown = {k: v for k, v in src.items() if k not in SECRET_KEYS.values()}
        return self.async_show_form(step_id=provider, errors=errors, data_schema=provider_schema(self.hass, provider, shown, self._key_required))

    async def async_step_anthropic(self, user_input=None):
        return await self._provider_step("anthropic", user_input)

    async def async_step_openai(self, user_input=None):
        return await self._provider_step("openai", user_input)

    async def async_step_ollama(self, user_input=None):
        return await self._provider_step("ollama", user_input)


class MembraneConfigFlow(_ProviderSteps, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 5               # 1.4: the old sensors and select are gone; 1.5: an entry runs its own seat again (mode inside), a 0.7.53 inside entry comes back as one

    async def _check(self, url: str, token: str, user: str = DEFAULT_SERVE_USER) -> str:
        """"" when the seat answers its health with this token, else the error key."""
        try:
            await SeatClient(async_get_clientsession(self.hass), url, token, user).health()
        except SeatAuthError:
            return "invalid_auth"
        except SeatError:
            return "cannot_connect"
        return ""

    async def async_step_hassio(self, discovery_info: HassioServiceInfo):
        """The add-on announced its seat through the Supervisor (service `membrane`, config {host, port, token}). One entry per Home Assistant,
        keyed by the seat (host:port). An entry of this seat, one typed by hand with its address, or one that ran the seat inside Home Assistant
        (no address yet) takes the announced address and token and is set up again: the add-on's token is the seat's word, no manual step.
        An entry of another seat is left alone. Otherwise the seat's health is checked with the token and the user confirms."""
        cfg = discovery_info.config or {}
        host, token = str(cfg.get("host") or "").strip(), str(cfg.get("token") or "").strip()
        url = clean_url(f"http://{host}:{cfg.get('port') or DEFAULT_PORT}") if host else ""
        uid = url.split("://", 1)[1] if url else ""
        if uid:
            await self.async_set_unique_id(uid)
        if not url or not token:
            return self.async_abort(reason="invalid_discovery")
        for entry in self._async_current_entries(include_ignore=False):
            if entry.data.get(CONF_MODE) == MODE_INSIDE:
                return self.async_abort(reason="already_configured")     # it runs its own seat: an add-on's seat does not take it over
            old = str(entry.data.get(CONF_SERVE_URL) or "")
            if entry.unique_id == uid or old in (url, ""):
                if old == url and entry.data.get(CONF_SERVE_PASS) == token and entry.unique_id == uid:
                    return self.async_abort(reason="already_configured")
                return self.async_update_reload_and_abort(entry, unique_id=uid, reason="already_configured",
                                                          data_updates={CONF_SERVE_URL: url, CONF_SERVE_PASS: token})
        self._abort_if_unique_id_configured()          # an ignored discovery of this seat stays ignored
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")       # one entry per Home Assistant: it names another seat
        err = await self._check(url, token)
        if err:
            return self.async_abort(reason=err)
        self._discovered = {"url": url, "token": token, "addon": discovery_info.name or "babyseat"}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input=None):
        d = self._discovered
        if user_input is None:                    # a form with no fields is a confirmation (HA's _set_confirm_only is a deprecated no-op)
            step_id, placeholders = ollama_hint(self.hass, "hassio_confirm", {"addon": d["addon"]})
            return self.async_show_form(step_id=step_id, description_placeholders=placeholders)
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")       # set up by hand while this confirm was open: one entry per Home Assistant
        return self.async_create_entry(title="Membrane", data={
            CONF_SERVE_URL: d["url"], CONF_SERVE_USER: DEFAULT_SERVE_USER, CONF_SERVE_PASS: d["token"]})

    async_step_hassio_confirm_ollama = async_step_hassio_confirm     # the same confirmation, shown with the Ollama hint

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)          # one entry per Home Assistant (the add-on's discovery keys its entry by host:port)
        self._abort_if_unique_id_configured()
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")
        return self.async_show_menu(step_id="user", menu_options=["inside", "connect"])

    async def async_step_inside(self, user_input=None):
        """Run a seat inside Home Assistant: the provider first (Ollama when Home Assistant has its own Ollama server, else Anthropic)."""
        try:
            release_target()
        except UnsupportedPlatform as e:
            return self.async_abort(reason="unsupported_platform", description_placeholders={"reason": str(e)})
        if user_input is not None and user_input.get(CONF_PROVIDER) in PROVIDERS:
            return await getattr(self, f"async_step_{user_input[CONF_PROVIDER]}")()
        return self.async_show_form(step_id="inside", data_schema=provider_picker("ollama" if ha_ollama(self.hass) else "anthropic"))

    async def _seat_done(self, settings: dict):
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")
        return self.async_create_entry(title="Membrane", data={CONF_MODE: MODE_INSIDE, **settings, CONF_SERVE_PORT: free_port(DEFAULT_PORT)})

    @staticmethod
    def async_get_options_flow(entry):
        return MembraneOptionsFlow()

    @classmethod
    def async_supports_options_flow(cls, entry) -> bool:
        return entry.data.get(CONF_MODE) == MODE_INSIDE

    async def async_step_connect(self, user_input=None):
        """Connect to a seat the person runs: its address and a token."""
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")
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
        step_id, placeholders = ollama_hint(self.hass, "connect", {})
        return self.async_show_form(step_id=step_id, errors=errors, description_placeholders=placeholders, data_schema=vol.Schema({
            vol.Required(CONF_SERVE_URL, default=(user_input or {}).get(CONF_SERVE_URL, "")): str,
            vol.Required(CONF_SERVE_PASS): str}))

    async_step_connect_ollama = async_step_connect                   # the same form, shown with the Ollama hint

    async def async_step_reauth(self, entry_data):
        """An entry with a seat address whose token was refused: a new token. An entry with none (it ran the seat inside Home Assistant,
        which this release no longer does): the address and the token of a seat that runs elsewhere."""
        if self._get_reauth_entry().data.get(CONF_MODE) == MODE_INSIDE:
            return await self.async_step_reauth_inside()
        if not str(self._get_reauth_entry().data.get(CONF_SERVE_URL) or ""):
            return await self.async_step_reauth_no_seat()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_inside(self, user_input=None):
        """The seat this entry runs refused its token (its key was changed or removed): a restart reads the token the seat writes at its start."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_inside")
        return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates={}, reason="reauth_successful")

    async def async_step_reauth_confirm(self, user_input=None):
        """The seat refused the token: it ran out, the seat's key changed, or it is a token of the old seat (0.7.53), which the new seat
        does not know."""
        return await self._reauth("reauth_confirm", user_input)

    async def async_step_reauth_no_seat(self, user_input=None):
        """The entry ran the seat inside Home Assistant: there is no seat to connect to until the user gives one."""
        return await self._reauth("reauth_no_seat", user_input)

    async def _reauth(self, step_id: str, user_input):
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
        placeholders = {"docs_url": DOCS_URL}
        if old_url:
            placeholders["url"] = old_url
        return self.async_show_form(step_id=step_id, errors=errors, data_schema=vol.Schema(fields), description_placeholders=placeholders)


class MembraneOptionsFlow(_ProviderSteps, config_entries.OptionsFlow):
    """The seat's settings of an inside entry: the provider, then its fields; an empty key keeps the stored one. The entry is reloaded (the seat restarts)."""
    _key_required = False

    async def async_step_init(self, user_input=None):
        self._keep = seat_settings(self.config_entry)
        if user_input is not None and user_input.get(CONF_PROVIDER) in PROVIDERS:
            return await getattr(self, f"async_step_{user_input[CONF_PROVIDER]}")()
        return self.async_show_form(step_id="init", data_schema=provider_picker(self._keep.get(CONF_PROVIDER) or "anthropic"))

    async def _seat_done(self, settings: dict):
        return self.async_create_entry(data=settings)
