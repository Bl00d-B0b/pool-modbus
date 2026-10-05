"""Covers: a cover a device opens and closes on command."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PoolModbusConfigEntry, PoolModbusCoordinator, PoolModbusData
from .entity import async_write
from .library import Cover


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    cover = data.device_type.cover
    if cover is not None:
        async_add_entities([PoolModbusCover(data, cover)])


class PoolModbusCover(CoordinatorEntity[PoolModbusCoordinator], CoverEntity):
    """Open or closed, as the device reports it; opening and closing are commands."""

    _attr_has_entity_name = True
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    def __init__(self, data: PoolModbusData, cover: Cover) -> None:
        super().__init__(data.coordinators[cover.scan_group])
        self.data = data
        self.cover = cover
        entry = self.coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{cover.key}"
        self._attr_name = cover.name
        self._attr_device_info = data.device_info
        if cover.device_class:
            self._attr_device_class = CoverDeviceClass(cover.device_class)

    @property
    def is_closed(self) -> bool | None:
        return self.cover.is_closed(self.coordinator.device)

    @property
    def icon(self) -> str | None:
        if self.cover.icon_closed is not None and self.is_closed:
            return self.cover.icon_closed
        return self.cover.icon

    async def async_open_cover(self, **kwargs: Any) -> None:
        device = self.coordinator.device
        await async_write(
            self.data, self.coordinator, "opening the cover", lambda: self.cover.open(device)
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        device = self.coordinator.device
        await async_write(
            self.data, self.coordinator, "closing the cover", lambda: self.cover.close(device)
        )
