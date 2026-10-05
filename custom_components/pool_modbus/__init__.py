"""Pool equipment over Modbus: one config entry per device."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.modbus import async_get_unit
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError

from .const import CONF_DEVICE_TYPE, CONF_UNIT_ID, DEFAULT_SCAN_INTERVAL
from .coordinator import PoolModbusConfigEntry, PoolModbusCoordinator, connection_config
from .library import get_device_type

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> bool:
    """Set up one device; devices with identical connection settings share a connection."""
    device_type = get_device_type(entry.data[CONF_DEVICE_TYPE])
    try:
        unit = async_get_unit(
            hass, entry, connection_config(entry.data).params(), int(entry.data[CONF_UNIT_ID])
        )
    except HomeAssistantError as err:
        raise ConfigEntryError(str(err)) from err

    coordinator = PoolModbusCoordinator(
        hass,
        entry,
        device_type,
        device_type.model(unit),
        timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> bool:
    """Unload a device; its shared connection closes with the last device on it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
