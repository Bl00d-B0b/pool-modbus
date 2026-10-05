"""Selects: values a device lets you pick from a list."""

from __future__ import annotations

from enum import Enum

from homeassistant.components.select import SelectEntity
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
        PoolModbusSelect(data, value) for value in data.values if value.writable and value.options
    )


class PoolModbusSelect(PoolModbusEntity, SelectEntity):
    """A setting picked from the choices its device type lists."""

    def __init__(self, data: PoolModbusData, value: Value) -> None:
        super().__init__(data, value)
        assert value.options is not None
        self._attr_options = list(value.options)

    @property
    def current_option(self) -> str | None:
        raw = self.device_value.get(self.coordinator.device)
        if raw is None:
            return None
        return getattr(raw, "label", raw.name) if isinstance(raw, Enum) else str(raw)

    async def async_select_option(self, option: str) -> None:
        await self.async_write_value(option)
