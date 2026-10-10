"""Pool equipment over Modbus: one config entry per device."""

from __future__ import annotations

from homeassistant.components.modbus import async_get_unit
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import entity_registry as er
from modbus_connection import ModbusError

from .const import CONF_DEVICE_TYPE, CONF_UNIT_ID
from .coordinator import (
    PoolModbusConfigEntry,
    PoolModbusCoordinator,
    PoolModbusData,
    connection_config,
    device_info,
    enabled_features,
)
from .library import get_device_type, group_model, read_plan

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]

RENAMED_KEYS = {"clock": "rtc", "sync_clock": "sync_rtc"}
"""Value and action keys renamed to the names other integrations use (RTC, Sync RTC)."""


async def async_setup_entry(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> bool:
    """Set up one device; devices with identical connection settings share a connection.

    Each scan group (fast, medium, slow) gets a model narrowed to the registers its
    entities use, read on that group's interval.
    """
    device_type = get_device_type(entry.data[CONF_DEVICE_TYPE])
    try:
        unit = async_get_unit(
            hass, entry, connection_config(entry.data).params(), int(entry.data[CONF_UNIT_ID])
        )
    except HomeAssistantError as err:
        raise ConfigEntryError(str(err)) from err

    # One full read: it identifies the firmware for the device info.
    full = device_type.model(unit)
    try:
        await full.async_update()
    except (ModbusError, OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"{entry.title} did not answer: {err}") from err

    features = enabled_features(device_type, entry.options)
    coordinators = {}
    for group, fields in read_plan(device_type, type(full), features).items():
        model = group_model(device_type, unit, fields)
        coordinator = PoolModbusCoordinator(hass, entry, model, group)
        await coordinator.async_config_entry_first_refresh()
        coordinators[group] = coordinator

    entry.runtime_data = PoolModbusData(
        device_type, features, coordinators, device_info(entry, device_type, full)
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await er.async_migrate_entries(hass, entry.entry_id, _renamed_unique_id(entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _renamed_unique_id(entry: PoolModbusConfigEntry):
    """Give entities of a renamed key their new unique id, keeping entity id and history."""
    prefix = f"{entry.unique_id or entry.entry_id}_"

    @callback
    def migrate(registered: er.RegistryEntry) -> dict[str, str] | None:
        key = registered.unique_id.removeprefix(prefix)
        if key == registered.unique_id or key not in RENAMED_KEYS:
            return None
        return {"new_unique_id": prefix + RENAMED_KEYS[key]}

    return migrate


async def _async_reload(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> bool:
    """Unload a device; its shared connection closes with the last device on it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
