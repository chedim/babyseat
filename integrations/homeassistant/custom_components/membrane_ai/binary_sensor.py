"""The field's binary readings (metrics.METRICS binary_sensor specs)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .metrics import METRICS
from .sensor import device_info


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(MembraneFlag(coord, entry, s) for s in METRICS
                       if s["platform"] == "binary_sensor")


class MembraneFlag(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord, entry, spec: dict) -> None:
        super().__init__(coord)
        self._key = spec["key"]
        self._attr_name = spec["name"]
        self._attr_unique_id = f"{entry.entry_id}_{spec['key']}"
        self._attr_icon = spec.get("icon")
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self):
        return (self.coordinator.data or {}).get(self._key)
