"""Membrane for Home Assistant (#1143): the seat as Assist's conversation agent (slice 1) and the seat's entities and services (slice 2).

The component runs a seat inside Home Assistant (operator 2026-11-15, the default: seat_runner.py downloads the seat of the component's own release,
checks it, starts `cliffvm serve` on 127.0.0.1 and keeps it running; an entry with `mode: inside`), or connects to a seat the user runs (cliffvm:
https://github.com/chedim/membrane) with its URL and a token. Either way it polls the seat's health and talks to it for Assist; it installs no pip package. Slice 2:
the seat's calls into the house (house.py: the component PULLS the calls the model wrote and runs them with Home Assistant's own
services), a few sensors and a select for the seat's model, and two services (`membrane.send_message`, `membrane.ask`). What the old
component also did (the seat as HA's child process, panels, the relay, the zoo) is later; see docs/home-assistant.md and docs/hass-plan.md.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import services
from .config_flow import seat_settings
from .const import (CONF_BACKEND, CONF_HA_USER_ID, CONF_MODE, CONF_MODEL, CONF_OLLAMA_URL, CONF_PROVIDER, CONF_RELEASE_BASE, CONF_SERVE_PASS,
                    CONF_SERVE_PORT, CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_PORT, DEFAULT_SERVE_USER, DOMAIN, ISSUE_SEAT_STOPPED, MODE_INSIDE, SEAT_DIR)
from .coordinator import SeatHealth
from .house import House, find_user
from .seat_client import SeatClient, SeatSessionGone
from .seat_options import SeatOptionsError, seat_facts
from .seat_runner import (APP_DIR, RELEASE_BASE, DistError, SeatProcess, SeatStartError, UnsupportedPlatform, component_version, ensure_dist,
                          free_port, port_free, release_target)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SELECT]
STALE_PLATFORMS = ("sensor", "binary_sensor", "select")     # the old component's entities (17 metrics, the seat select): none of them exists on cliffvm


class SeatData:
    """What a loaded entry holds (entry.runtime_data): the client, the health poll, the seat's use of the house, the seat session of each HA conversation."""

    def __init__(self, client: SeatClient, health: SeatHealth, house: House, seat: SeatProcess | None = None) -> None:
        self.client = client
        self.health = health
        self.house = house
        self.seat = seat                          # the seat this entry runs (mode inside), else None
        self.sessions: dict[str, str] = {}

    async def say(self, key: str, text: str, speaker: str = "", wait: bool = True):
        """One turn on the seat session of `key` (a conversation id, or a service's session name), made on first use. `wait`: the answer's text; else None (the seat answers on
        its own). A session the seat does not have any more (its store was reset) is made again, once."""
        send = self.client.message if wait else self.client.prompt
        sid = self.sessions.get(key) or await self.client.create_session(key)
        self.sessions[key] = sid
        try:
            return await send(sid, text, speaker)
        except SeatSessionGone:
            sid = self.sessions[key] = await self.client.create_session(key)
            return await send(sid, text, speaker)


def _zoo_seat_url(seat: dict) -> str:
    """The old component's seat list: a seat's own URL, else http://host:serve_port."""
    if seat.get("url"):
        return str(seat["url"]).rstrip("/")
    host = str(seat.get("host") or "").strip()
    return f"http://{host}:{int(seat.get('serve_port') or 4096)}" if host else ""


def migrated_data(data: dict, options: dict) -> dict:
    """The data of an entry from what the old component kept (1.1: the seat in the data; 1.2: a list of seats in the options, one
    active). The active connect seat's address and token are kept. An entry that ran the seat inside Home Assistant comes back as an inside
    entry on the Ollama server it thought with (0.7.53's `backend` and its model, the options' pick first): the setup starts the seat again,
    nothing for the person to do. One without a backend has nothing to run: an empty address and token, which the setup turns into a reauth."""
    opts, data = options or {}, data or {}
    seats = [s for s in opts.get("seats") or [] if isinstance(s, dict)]
    chosen = next((s for s in seats if s.get("id") == opts.get("active")), None) or (seats[0] if seats else None)
    inside = (chosen is not None and chosen.get("inside")) or (chosen is None and data.get(CONF_MODE) == MODE_INSIDE)
    backend = str(data.get(CONF_BACKEND) or "").strip().rstrip("/")
    model = str(opts.get(CONF_MODEL) or data.get(CONF_MODEL) or "").strip()
    if inside and backend and model:
        return {CONF_MODE: MODE_INSIDE, CONF_PROVIDER: "ollama", CONF_OLLAMA_URL: backend, CONF_MODEL: model}
    if chosen is not None and not chosen.get("inside"):
        url, user, token = _zoo_seat_url(chosen), chosen.get("user") or data.get(CONF_SERVE_USER), chosen.get("token")
    elif chosen is not None or data.get("mode") == "inside":
        url, user, token = "", None, ""
    else:
        url, user, token = data.get(CONF_SERVE_URL), data.get(CONF_SERVE_USER), opts.get(CONF_SERVE_PASS, data.get(CONF_SERVE_PASS))
    return {CONF_SERVE_URL: str(url or "").rstrip("/"), CONF_SERVE_USER: str(user or DEFAULT_SERVE_USER),
            CONF_SERVE_PASS: str(token or "")}


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.x -> 1.3: the entry keeps the seat's URL and token, or (it ran the seat inside Home Assistant) becomes an inside entry on its Ollama server
    (migrated_data); the seat list and the options of the old component are gone with the features they set up. 1.3 -> 1.4: the old component's
    sensors, binary sensor and seat select are removed from the entity registry here (they would show as unavailable for ever): slice 2 makes its own
    few from the seat's rows, so none of the old ones continues. 1.4 -> 1.5: the inside mode is back (nothing to change in a 1.4 entry)."""
    if entry.version == 1 and entry.minor_version < 3:
        hass.config_entries.async_update_entry(entry, data=migrated_data(dict(entry.data), dict(entry.options)), options={},
                                               minor_version=3)
    if entry.version == 1 and entry.minor_version < 4:
        registry = er.async_get(hass)
        for e in er.async_entries_for_config_entry(registry, entry.entry_id):
            if e.domain in STALE_PLATFORMS:
                registry.async_remove(e.entity_id)
        hass.config_entries.async_update_entry(entry, minor_version=4)
    if entry.version == 1 and entry.minor_version < 5:
        hass.config_entries.async_update_entry(entry, minor_version=5)
    return True


def _seat_stopped(hass: HomeAssistant, entry: ConfigEntry, reason: str) -> None:
    """The runner gave up on the seat: a repair says why (the seat's own words, secrets masked) and that a reload tries again."""
    ir.async_create_issue(hass, DOMAIN, f"{ISSUE_SEAT_STOPPED}_{entry.entry_id}", is_fixable=False, severity=ir.IssueSeverity.ERROR,
                          translation_key=ISSUE_SEAT_STOPPED, translation_placeholders={"reason": reason[:500]})


async def _start_seat(hass: HomeAssistant, entry: ConfigEntry) -> SeatProcess:
    """The seat of an inside entry, started: the settings checked (ConfigEntryError), the host's build (ConfigEntryError), the release in place
    (ConfigEntryNotReady: Home Assistant retries), a port (the entry's when it is free, else a new one kept in the entry), the process up and healthy."""
    settings = seat_settings(entry)
    try:
        seat_facts(settings)
        target = release_target()
    except (SeatOptionsError, UnsupportedPlatform) as e:
        raise ConfigEntryError(str(e)) from e
    base = Path(hass.config.path(SEAT_DIR))
    version = await hass.async_add_executor_job(component_version)
    session = async_get_clientsession(hass)
    try:
        dist = await ensure_dist(session, hass.async_add_executor_job, base / APP_DIR, version, target,
                                 entry.data.get(CONF_RELEASE_BASE) or RELEASE_BASE.format(version=version))
    except DistError as e:
        raise ConfigEntryNotReady(str(e)) from e
    seat = SeatProcess(dist / "bin" / "cliffvm", base, int(entry.data.get(CONF_SERVE_PORT) or DEFAULT_PORT), settings,
                       run_blocking=hass.async_add_executor_job, session=session, on_give_up=lambda why: _seat_stopped(hass, entry, why))
    await seat.reap_orphan()
    if not await hass.async_add_executor_job(port_free, seat.port):
        seat.port = await hass.async_add_executor_job(free_port, DEFAULT_PORT)
        _LOGGER.warning("membrane: the seat's port was taken: it moves to %s", seat.port)
    if seat.port != entry.data.get(CONF_SERVE_PORT):
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_SERVE_PORT: seat.port})
    try:
        await seat.start()
    except SeatStartError as e:
        raise ConfigEntryNotReady(str(e)) from e
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_SEAT_STOPPED}_{entry.entry_id}")
    return seat


async def _settings_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The options flow changed the seat's settings: the entry is reloaded (the seat restarts with them). Other updates (the hidden user's id) are not."""
    data = getattr(entry, "runtime_data", None)
    if data is not None and data.seat is not None and seat_settings(entry) != data.seat.settings:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    seat = None
    if entry.data.get(CONF_MODE) == MODE_INSIDE:
        seat = await _start_seat(hass, entry)
        url, token = seat.url, seat.token
    else:
        url, token = str(entry.data.get(CONF_SERVE_URL) or ""), entry.data.get(CONF_SERVE_PASS)
        if urlsplit(url).scheme not in ("http", "https") or not token:
            raise ConfigEntryAuthFailed("no seat address or token: give the address and token of a seat that runs elsewhere, or set the "
                                        "integration up again with a seat inside Home Assistant")   # reauth (reauth_no_seat) asks both
    try:
        client = SeatClient(async_get_clientsession(hass), url, token, entry.data.get(CONF_SERVE_USER) or DEFAULT_SERVE_USER)
        if seat is not None:
            seat.on_token = client.set_token               # the runner's fresh token (it mints one a day) is the one the client sends
        health = SeatHealth(hass, entry, client)
        await health.async_config_entry_first_refresh()          # ConfigEntryAuthFailed -> reauth; the seat down -> HA retries
        house = House(hass, client, entry)
        await house.ensure_user()                                 # the user every call runs as: created once, found again after
        entry.runtime_data = SeatData(client, health, house, seat)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        if seat is not None:
            await seat.stop()                                     # a failed setup leaves no seat running
        raise
    services.register(hass, entry)
    entry.async_create_background_task(hass, house.run(), "membrane house")   # Home Assistant ends it with the entry
    if seat is not None:
        async def _ha_stops(_event) -> None:
            await seat.stop()
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _ha_stops))
        entry.async_on_unload(entry.add_update_listener(_settings_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    services.unregister(hass)
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data = getattr(entry, "runtime_data", None)
    if data is not None and data.seat is not None:
        await data.seat.stop()                    # before a reload starts the next one: one seat on the data folder and the port at a time
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration is removed: the hidden user goes with it (a removal that finds none is quiet)."""
    user = await find_user(hass, entry.data.get(CONF_HA_USER_ID))
    if user is not None:
        await hass.auth.async_remove_user(user)
