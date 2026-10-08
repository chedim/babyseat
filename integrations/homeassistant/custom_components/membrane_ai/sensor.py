"""The field metrics as sensors (one per metrics.METRICS sensor spec)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .metrics import METRICS


def device_info(entry) -> dict:
    return {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Membrane brain",
            "manufacturer": "membrane", "model": "field seat"}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coord = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(MembraneMetric(coord, entry, s) for s in METRICS
                       if s["platform"] == "sensor")


class MembraneMetric(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coord, entry, spec: dict) -> None:
        super().__init__(coord)
        self._key = spec["key"]
        self._attr_name = spec["name"]
        self._attr_unique_id = f"{entry.entry_id}_{spec['key']}"
        self._attr_icon = spec.get("icon")
        self._attr_native_unit_of_measurement = spec.get("unit")
        if spec.get("state_class"):
            self._attr_state_class = SensorStateClass(spec["state_class"])
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._key)
