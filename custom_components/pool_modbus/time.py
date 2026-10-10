"""Times of day: settings a device keeps as a time, such as when a schedule runs."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PoolModbusConfigEntry
from .entity import PoolModbusEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        PoolModbusTime(data, value) for value in data.values if value.time_of_day and value.writable
    )


class PoolModbusTime(PoolModbusEntity, TimeEntity):
    """A time of day the device keeps."""

    entity_domain = Platform.TIME

    @property
    def native_value(self) -> time | None:
        raw = self.device_value.get(self.coordinator.device)
        return raw if isinstance(raw, time) else None

    async def async_set_value(self, value: time) -> None:
        await self.async_write_value(value)
