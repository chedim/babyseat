"""The seat's health, polled (POLL_S): `GET /global/health`. A 401 is the token's end of life: Home Assistant starts its reauth flow
(the user pastes a new token); any other failure makes the entry retry / unavailable until the seat answers again."""
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
            return await self._client.health()
        except SeatAuthError as e:
            raise ConfigEntryAuthFailed(str(e)) from e
        except SeatError as e:
            raise UpdateFailed(str(e)) from e
