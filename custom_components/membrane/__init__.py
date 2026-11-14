"""Membrane for Home Assistant (#1143, slice 1): the seat as Assist's conversation agent.

The component is a thin client of a seat the user already runs (cliffvm: https://github.com/chedim/membrane): it connects to the
seat's URL with a token, polls its health, and talks to it for Assist. It does not start a seat and installs no package. What the old
component also did (the seat as HA's child process, panels, the relay, sensors, the zoo, the seat acting on HA) is later slices;
see docs/home-assistant.md and docs/hass-plan.md.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SERVE_PASS, CONF_SERVE_URL, CONF_SERVE_USER, DEFAULT_SERVE_USER
from .coordinator import SeatHealth
from .seat_client import SeatClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CONVERSATION]


class SeatData:
    """What a loaded entry holds (entry.runtime_data): the client, the health poll, the seat session of each HA conversation."""

    def __init__(self, client: SeatClient, health: SeatHealth) -> None:
        self.client = client
        self.health = health
        self.sessions: dict[str, str] = {}


def _zoo_seat_url(seat: dict) -> str:
    """The old component's seat list: a seat's own URL, else http://host:serve_port."""
    if seat.get("url"):
        return str(seat["url"]).rstrip("/")
    host = str(seat.get("host") or "").strip()
    return f"http://{host}:{int(seat.get('serve_port') or 4096)}" if host else ""


def migrated_data(data: dict, options: dict) -> dict:
    """The data of a 1.3 entry from what the old component kept (1.1: the seat in the data; 1.2: a list of seats in the options, one
    active). The active connect seat's address and token are kept; an entry that ran the seat inside Home Assistant has none to
    connect to: it gets an empty address and token, which the setup turns into a reauth (the user gives a seat address and a token)."""
    opts, data = options or {}, data or {}
    seats = [s for s in opts.get("seats") or [] if isinstance(s, dict)]
    chosen = next((s for s in seats if s.get("id") == opts.get("active")), None) or (seats[0] if seats else None)
    if chosen is not None and not chosen.get("inside"):
        url, user, token = _zoo_seat_url(chosen), chosen.get("user") or data.get(CONF_SERVE_USER), chosen.get("token")
    elif chosen is not None or data.get("mode") == "inside":
        url, user, token = "", None, ""
    else:
        url, user, token = data.get(CONF_SERVE_URL), data.get(CONF_SERVE_USER), opts.get(CONF_SERVE_PASS, data.get(CONF_SERVE_PASS))
    return {CONF_SERVE_URL: str(url or "").rstrip("/"), CONF_SERVE_USER: str(user or DEFAULT_SERVE_USER),
            CONF_SERVE_PASS: str(token or "")}


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.x -> 1.3: connect-only (the entry keeps the seat's URL and token; the seat list, the inside mode and the options of the old
    component are gone with the features they set up)."""
    if entry.version == 1 and entry.minor_version < 3:
        hass.config_entries.async_update_entry(entry, data=migrated_data(dict(entry.data), dict(entry.options)), options={},
                                               minor_version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    url = str(entry.data.get(CONF_SERVE_URL) or "")
    if urlsplit(url).scheme not in ("http", "https") or not entry.data.get(CONF_SERVE_PASS):
        raise ConfigEntryAuthFailed("no seat address or token: give them again")       # an entry from inside mode: reauth asks both
    client = SeatClient(async_get_clientsession(hass), url, entry.data[CONF_SERVE_PASS],
                        entry.data.get(CONF_SERVE_USER) or DEFAULT_SERVE_USER)
    health = SeatHealth(hass, entry, client)
    await health.async_config_entry_first_refresh()          # ConfigEntryAuthFailed -> reauth; the seat down -> HA retries
    entry.runtime_data = SeatData(client, health)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
