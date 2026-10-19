"""The setup form: run the seat inside Home Assistant (it thinks with an
Ollama server -- models picked from its list) or connect to a seat that runs
elsewhere (the boat's way: a separate brain machine). The options add more
seats to the house (#283, the zoo: zoo.py)."""
from __future__ import annotations

import asyncio

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig, SelectSelectorMode

from . import zoo
from .const import (CONF_BACKEND, CONF_EMBED_MODEL, CONF_MODE, CONF_MODEL, CONF_SERVE_PASS,
                    CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_BACKEND, DEFAULT_LOOPS,
                    DEFAULT_SERVE_USER, DOMAIN, MODE_CONNECT, MODE_INSIDE, OPT_HA_ACCESS,
                    OPT_HA_BASE_URL, OPT_LANDING, OPT_LOOPS, OPT_STT_URL)

EMBED_MARKERS = ("embed", "minilm", "bge", "e5-", "gte")


async def ollama_models(hass, url: str) -> list[str]:
    s = async_get_clientsession(hass)
    async with s.get(url.rstrip("/") + "/api/tags", timeout=aiohttp.ClientTimeout(total=10)) as r:
        r.raise_for_status()
        d = await r.json()
    return sorted(str(m.get("name") or "") for m in d.get("models") or [] if m.get("name"))


def ha_ollama(hass) -> tuple[str, str] | None:
    """Home Assistant's own Ollama integration, when it has one: (its server URL,
    the model its conversation agent uses). The seat thinks with the same server."""
    for e in hass.config_entries.async_entries("ollama"):
        url = str((e.data or {}).get("url") or "")
        if not url:
            continue
        model = str((e.data or {}).get("model") or (e.options or {}).get("model") or "")
        for sub in (getattr(e, "subentries", None) or {}).values():
            if getattr(sub, "subentry_type", "") == "conversation" and (sub.data or {}).get("model"):
                model = str(sub.data["model"])
                break
        return url.rstrip("/"), model
    return None


NOT_CHAT = ("whisper", "tts", "vision-encoder")       # speech and other non-chat models
CHAT_PREF = ("glm", "qwen3", "gpt-oss", "qwen2.5", "llama3.1", "llama3", "mistral", "gemma")   # the seat's own order
EMBED_PREF = ("mxbai-embed-large", "nomic-embed-text", "qwen3-embedding", "bge", "all-minilm")


def _rank(names: list[str], prefs: tuple) -> list[str]:
    def key(n):
        low = n.lower()
        return (next((i for i, p in enumerate(prefs) if low.startswith(p) or "/" + p in low), len(prefs)), low)
    return sorted(names, key=key)


def split_models(names: list[str], first: str = "") -> tuple[list[str], list[str]]:
    """(chat models, embedding models), best first; `first` (HA's own agent's) leads."""
    emb = [n for n in names if any(k in n.lower() for k in EMBED_MARKERS)]
    chat = [n for n in names if n not in emb and not any(k in n.lower() for k in NOT_CHAT)]
    chat = _rank(chat, CHAT_PREF)
    if first in chat:
        chat = [first] + [m for m in chat if m != first]
    return chat, _rank(emb, EMBED_PREF)


class MembraneConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 2               # #283: the seats list (async_migrate_entry)

    def __init__(self):
        self._backend = DEFAULT_BACKEND
        self._models: list[str] = []
        self._ha_model = ""             # the model HA's Ollama integration talks with
        self._found = False

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)          # one entry per Home Assistant; more seats: its options (#283)
        self._abort_if_unique_id_configured()
        return self.async_show_menu(step_id="user", menu_options=[MODE_INSIDE, MODE_CONNECT])

    async def async_step_inside(self, user_input=None):
        errors = {}
        found = ha_ollama(self.hass) if user_input is None else None
        if found:
            # Home Assistant already talks to an Ollama server: the seat uses it too
            # (no URL to type); the form below stays for a server that doesn't answer
            self._backend, self._ha_model = found
            try:
                self._models = await ollama_models(self.hass, self._backend)
            except Exception:                            # noqa: BLE001
                errors["base"] = "no_ollama"
            else:
                if self._models:
                    self._found = True
                    return await self.async_step_models()
                errors["base"] = "no_models"
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
        chat, emb = split_models(self._models, self._ha_model)   # HA's own agent's model first
        if user_input is not None:
            return self.async_create_entry(title="Membrane", data={
                CONF_MODE: MODE_INSIDE, CONF_BACKEND: self._backend,
                CONF_MODEL: user_input[CONF_MODEL], CONF_EMBED_MODEL: user_input[CONF_EMBED_MODEL]})
        pick = lambda opts: SelectSelector(SelectSelectorConfig(options=opts or self._models,
                                                                mode=SelectSelectorMode.DROPDOWN))
        return self.async_show_form(step_id="models", data_schema=vol.Schema({
            vol.Required(CONF_MODEL, default=(chat or self._models)[0]): pick(chat),
            vol.Required(CONF_EMBED_MODEL, default=(emb or self._models)[0]): pick(emb),
        }), description_placeholders={
            "backend": self._backend,
            "source": "Home Assistant's Ollama integration" if self._found else "the server you gave"})

    async def async_step_connect(self, user_input=None):
        errors = {}
        if user_input is not None:
            s = async_get_clientsession(self.hass)
            pw = user_input.get(CONF_SERVE_PASS) or ""
            try:
                async with s.get(user_input[CONF_SERVE_URL].rstrip("/") + "/global/health",
                                 auth=(aiohttp.BasicAuth(user_input.get(CONF_SERVE_USER) or DEFAULT_SERVE_USER, pw)
                                       if pw else None),     # the seat token (#253), or a proxy's pair
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    ok = r.status == 200
            except Exception:                            # noqa: BLE001
                ok = False
            if ok:
                return self.async_create_entry(title="Membrane", data={CONF_MODE: MODE_CONNECT, **user_input})
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="connect", errors=errors, data_schema=vol.Schema({
            vol.Required(CONF_SERVE_URL): str,
            vol.Optional(CONF_SERVE_USER, default=DEFAULT_SERVE_USER): str,
            vol.Optional(CONF_SERVE_PASS, default=""): str}))

    @staticmethod
    @callback
    def async_get_options_flow(entry):
        return MembraneOptionsFlow()


class MembraneOptionsFlow(config_entries.OptionsFlow):
    """The settings (the models from the same Ollama server's list, the landing
    page, speech, the loops -- the seat restarts with them) and THE ZOO's seats
    (#283): add one (host, ports, its seat token, a name), edit, remove."""

    def __init__(self):
        self._edit = None

    def _opts(self) -> dict:
        return zoo.migrate(dict(self.config_entry.data), dict(self.config_entry.options))

    def _pick(self, seats: list) -> SelectSelector:
        return SelectSelector(SelectSelectorConfig(
            options=[{"value": s["id"], "label": n} for s, n in zip(seats, zoo.names(seats))],
            mode=SelectSelectorMode.DROPDOWN))

    async def async_step_init(self, user_input=None):
        seats = self._opts()[zoo.SEATS]
        menu = ["settings", "add_seat", "pick_seat"]
        if len(seats) > 1:
            menu.append("remove_seat")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data={**self._opts(), **user_input})
        o, d = self.config_entry.options, self.config_entry.data
        fields = {}
        if d.get(CONF_MODE) == MODE_INSIDE:
            cur_m = o.get(CONF_MODEL) or d.get(CONF_MODEL, "")
            cur_e = o.get(CONF_EMBED_MODEL) or d.get(CONF_EMBED_MODEL, "")
            try:
                names = await ollama_models(self.hass, d[CONF_BACKEND])
            except Exception:                            # noqa: BLE001
                names = []
            chat, emb = split_models(names, cur_m)
            chat = chat if cur_m in chat or not cur_m else [cur_m] + chat
            emb = emb if cur_e in emb or not cur_e else [cur_e] + emb
            pick = lambda opts: SelectSelector(SelectSelectorConfig(options=opts, mode=SelectSelectorMode.DROPDOWN))
            if chat:
                fields[vol.Required(CONF_MODEL, default=cur_m or chat[0])] = pick(chat)
            if emb:
                fields[vol.Required(CONF_EMBED_MODEL, default=cur_e or emb[0])] = pick(emb)
            fields[vol.Required(OPT_HA_ACCESS, default=o.get(OPT_HA_ACCESS, True))] = bool
        else:
            # CONNECT (detached): no model pickers (the seat thinks with its own
            # backend); what Home Assistant gives the seat is configurable here.
            # The seat token (#253) is the seat's own now: Seats -> edit (#283)
            fields[vol.Required(OPT_HA_ACCESS, default=o.get(OPT_HA_ACCESS, True))] = bool
            fields[vol.Optional(OPT_HA_BASE_URL, default=o.get(OPT_HA_BASE_URL, ""))] = str
        return self.async_show_form(step_id="settings", data_schema=vol.Schema({
            **fields,
            vol.Required(OPT_LANDING, default=o.get(OPT_LANDING, True)): bool,
            vol.Optional(OPT_STT_URL, default=o.get(OPT_STT_URL, "")): str,
            vol.Required(OPT_LOOPS, default=o.get(OPT_LOOPS, DEFAULT_LOOPS)): vol.All(int, vol.Range(min=1, max=3)),
        }))

    async def _card_name(self, seat: dict) -> str:
        """No name given: the seat's Blurp's own (its card or sheet), else its host."""
        try:
            card = await asyncio.wait_for(zoo.fetch_card(async_get_clientsession(self.hass),
                                                         zoo.conn_of({}, seat)), zoo.CARD_TIMEOUT_S)
        except Exception:                                # noqa: BLE001 (an offline seat can still be added)
            card = {}
        return str(card.get("name") or seat.get("host") or "")

    def _seat_schema(self, seat: dict | None) -> vol.Schema:
        s = seat or {}
        if s.get("inside"):
            return vol.Schema({vol.Optional("name", default=s.get("name", "")): str,
                               vol.Optional(OPT_STT_URL, default=s.get(OPT_STT_URL, "")): str})
        return vol.Schema({
            vol.Required("host", default=s.get("host", "")): str,
            vol.Required("serve_port", default=int(s.get("serve_port") or zoo.DEFAULT_SERVE_PORT)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required("proxy_port", default=int(s.get("proxy_port") or zoo.DEFAULT_PROXY_PORT)):
                vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional("token", default=s.get("token", "")): str,
            vol.Optional("name", default=s.get("name", "")): str,
            vol.Optional(OPT_STT_URL, default=s.get(OPT_STT_URL, "")): str})

    async def async_step_add_seat(self, user_input=None):
        errors = {}
        if user_input is not None:
            opts = self._opts()
            seat = zoo.seat_from_form(user_input, opts[zoo.SEATS])
            if not seat["host"]:
                errors["host"] = "no_host"
            else:
                if not seat["name"]:
                    seat["name"] = await self._card_name(seat)
                opts[zoo.SEATS] = opts[zoo.SEATS] + [seat]
                return self.async_create_entry(data=opts)
        return self.async_show_form(step_id="add_seat", data_schema=self._seat_schema(None), errors=errors)

    async def async_step_pick_seat(self, user_input=None):
        seats = self._opts()[zoo.SEATS]
        if user_input is not None:
            self._edit = user_input["seat"]
            return await self.async_step_edit_seat()
        return self.async_show_form(step_id="pick_seat", data_schema=vol.Schema({
            vol.Required("seat", default=seats[0]["id"]): self._pick(seats)}))

    async def async_step_edit_seat(self, user_input=None):
        opts = self._opts()
        old = zoo.find(opts[zoo.SEATS], self._edit)
        if old is None:
            return self.async_abort(reason="no_seat")
        if user_input is not None:
            if old.get("inside"):
                new = {**old, "name": str(user_input.get("name") or "").strip() or zoo.INSIDE_NAME}
                if user_input.get(OPT_STT_URL):
                    new[OPT_STT_URL] = user_input[OPT_STT_URL]
                else:
                    new.pop(OPT_STT_URL, None)
            else:
                new = zoo.seat_from_form(user_input, opts[zoo.SEATS], old)
                if not new["name"]:
                    new["name"] = await self._card_name(new)
            opts[zoo.SEATS] = [new if s.get("id") == old.get("id") else s for s in opts[zoo.SEATS]]
            return self.async_create_entry(data=opts)
        return self.async_show_form(step_id="edit_seat", data_schema=self._seat_schema(old),
                                    description_placeholders={"seat": zoo.display_name(old)})

    async def async_step_remove_seat(self, user_input=None):
        opts = self._opts()
        gone = [s for s in opts[zoo.SEATS] if not s.get("inside")]
        if user_input is not None:
            return self.async_create_entry(data=zoo.remove(opts, user_input["seat"]))
        return self.async_show_form(step_id="remove_seat", data_schema=vol.Schema({
            vol.Required("seat"): self._pick(gone)}))
