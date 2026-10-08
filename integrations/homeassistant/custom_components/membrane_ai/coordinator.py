"""The field metrics poller: the serve's relayed /membrane/state + /agent
every 60s (the seat caches its state 60s anyway) -> metrics.metrics_from."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (DataUpdateCoordinator,
                                                      UpdateFailed)

from .metrics import metrics_from

_LOGGER = logging.getLogger(__name__)
POLL = timedelta(seconds=60)


class FieldCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, entry, comp: dict) -> None:
        super().__init__(hass, _LOGGER, name="membrane field", update_interval=POLL,
                         config_entry=entry)
        self._comp = comp

    async def _get(self, path: str):
        c = self._comp
        sess = async_get_clientsession(self.hass)
        async with sess.get(c["serve_url"].rstrip("/") + path,
                            auth=aiohttp.BasicAuth(c["serve_user"], c["serve_pass"]),
                            timeout=aiohttp.ClientTimeout(total=50)) as r:
            if r.status != 200:
                raise UpdateFailed(f"{path} answered {r.status}")
            return await r.json(content_type=None)

    async def _async_update_data(self) -> dict:
        try:
            state = await self._get("/membrane/state")
            try:
                agents = await self._get("/agent")
            except Exception:                       # noqa: BLE001 (minds are optional)
                agents = []
        except UpdateFailed:
            raise
        except Exception as e:                      # noqa: BLE001
            raise UpdateFailed(f"brain unreachable: {e!r}") from e
        return metrics_from(state, agents)
