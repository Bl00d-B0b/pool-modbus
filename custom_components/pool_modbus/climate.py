"""Climate: a device that works as a thermostat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import PoolModbusConfigEntry, PoolModbusCoordinator, PoolModbusData
from .entity import async_write
from .library import Thermostat


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PoolModbusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    thermostat = data.device_type.thermostat
    if thermostat is not None:
        async_add_entities([PoolModbusClimate(data, thermostat)])


class PoolModbusClimate(CoordinatorEntity[PoolModbusCoordinator], ClimateEntity):
    """The device's thermostat: mode, target and current temperature."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, data: PoolModbusData, thermostat: Thermostat) -> None:
        super().__init__(data.coordinators[thermostat.scan_group])
        self.data = data
        self.thermostat = thermostat
        entry = self.coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{thermostat.key}"
        self._attr_name = thermostat.name
        self._attr_device_info = data.device_info
        self._attr_hvac_modes = [HVACMode(mode) for mode in thermostat.modes]
        self._attr_min_temp = thermostat.minimum
        self._attr_max_temp = thermostat.maximum
        self._attr_target_temperature_step = thermostat.step

    @property
    def current_temperature(self) -> float | None:
        return self.thermostat.current_temperature(self.coordinator.device)

    @property
    def target_temperature(self) -> float | None:
        return self.thermostat.target_temperature(self.coordinator.device)

    @property
    def hvac_mode(self) -> HVACMode | None:
        mode = self.thermostat.mode(self.coordinator.device)
        return None if mode is None else HVACMode(mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        action = self.thermostat.action(self.coordinator.device)
        return None if action is None else HVACAction(action)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            device = self.coordinator.device
            await async_write(
                self.data,
                self.coordinator,
                "the target temperature",
                lambda: self.thermostat.set_target_temperature(device, temperature),
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        device = self.coordinator.device
        await async_write(
            self.data,
            self.coordinator,
            "the thermostat mode",
            lambda: self.thermostat.set_mode(device, str(hvac_mode)),
        )

    async def async_turn_on(self) -> None:
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)
