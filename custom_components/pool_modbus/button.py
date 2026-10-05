"""Buttons: things a device does when told."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import PoolModbusConfigEntry, PoolModbusCoordinator, PoolModbusData
from .entity import async_write
from .library import Action

_CATEGORIES = {"setting": EntityCategory.CONFIG, "diagnostic": EntityCategory.DIAGNOSTIC}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    async_add_entities(PoolModbusButton(data, action) for action in data.actions)


class PoolModbusButton(CoordinatorEntity[PoolModbusCoordinator], ButtonEntity):
    """One of the device type's actions."""

    _attr_has_entity_name = True

    def __init__(self, data: PoolModbusData, action: Action) -> None:
        super().__init__(data.coordinators[action.scan_group])
        self.data = data
        self.action = action
        entry = self.coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{action.key}"
        self._attr_name = action.name
        self._attr_device_info = data.device_info
        self._attr_icon = action.icon
        self._attr_entity_category = _CATEGORIES.get(action.category)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        check = self.action.available
        return check is None or bool(check(self.coordinator.device))

    async def async_press(self) -> None:
        device = self.coordinator.device
        await async_write(
            self.data,
            self.coordinator,
            self.action.name,
            lambda: self.action.press(device, dt_util.now()),
        )
