"""Switches: every on/off value a device lets you change."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
        PoolModbusSwitch(data, value) for value in data.values if value.binary and value.writable
    )


class PoolModbusSwitch(PoolModbusEntity, SwitchEntity):
    """An on/off setting, written to the device."""

    def __init__(self, data: PoolModbusData, value: Value) -> None:
        super().__init__(data, value)
        if value.device_class:
            self._attr_device_class = SwitchDeviceClass(value.device_class)

    @property
    def is_on(self) -> bool | None:
        raw = self.device_value.get(self.coordinator.device)
        return None if raw is None else bool(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write_value(False)
