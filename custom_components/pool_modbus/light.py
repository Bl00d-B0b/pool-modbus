"""Lights: on/off values a device switches as a light."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
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
        PoolModbusLight(data, value) for value in data.values if value.light and value.writable
    )


class PoolModbusLight(PoolModbusEntity, LightEntity):
    """A light that is only on or off."""

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    @property
    def is_on(self) -> bool | None:
        raw = self.device_value.get(self.coordinator.device)
        return None if raw is None else bool(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write_value(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write_value(False)
