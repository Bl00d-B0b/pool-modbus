"""Texts: settings a device keeps as a string, such as its name."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import PoolModbusConfigEntry, PoolModbusData
from .entity import PoolModbusEntity
from .library import Value


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(
        PoolModbusText(data, value)
        for value in data.values
        if value.writable and value.text_pattern is not None
    )


class PoolModbusText(PoolModbusEntity, TextEntity):
    """A text the device keeps, limited to the characters and length it accepts."""

    entity_domain = Platform.TEXT
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0

    def __init__(self, data: PoolModbusData, value: Value) -> None:
        super().__init__(data, value)
        self._attr_pattern = value.text_pattern
        if value.minimum is not None:
            self._attr_native_min = int(value.minimum)
        if value.maximum is not None:
            self._attr_native_max = int(value.maximum)

    @property
    def native_value(self) -> str | None:
        raw = self.device_value.get(self.coordinator.device)
        return raw if isinstance(raw, str) else None

    async def async_set_value(self, value: str) -> None:
        await self.async_write_value(value)
