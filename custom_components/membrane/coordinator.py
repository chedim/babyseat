"""The seat, polled (POLL_S): `GET /global/health` (a 401 is the token's end of life: Home Assistant starts its reauth flow; any other failure makes the entry retry /
unavailable until the seat answers again), and, for the entities of slice 2, the seat's own rows (`GET /membrane/ha/metrics`) and options (`GET /membrane/options`). The two
extra reads are the owner's: a seat that refuses them (the token is a person's) or has no such route just has no rows and no model list, and Assist is untouched."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import POLL_S
from .seat_client import SeatAuthError, SeatClient, SeatError

_LOGGER = logging.getLogger(__name__)


class SeatHealth(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry, client: SeatClient) -> None:
        super().__init__(hass, _LOGGER, name="membrane seat", update_interval=timedelta(seconds=POLL_S), config_entry=entry)
        self._client = client

    async def _async_update_data(self) -> dict:
        try:
            health = await self._client.health()
        except SeatAuthError as e:
            raise ConfigEntryAuthFailed(str(e)) from e
        except SeatError as e:
            raise UpdateFailed(str(e)) from e
        rows, options = [], {}
        for key, read in (("rows", self._client.metrics), ("options", self._client.options)):
            try:
                got = await read()
            except SeatAuthError as e:
                raise ConfigEntryAuthFailed(str(e)) from e
            except SeatError as e:                   # not the owner's token (metrics), or a seat without the pack: nothing of it
                _LOGGER.debug("membrane: no %s from the seat: %s", key, e)
                continue
            if key == "rows":
                rows = got
            else:
                options = got
        return {"health": health, "rows": rows, "options": options}
