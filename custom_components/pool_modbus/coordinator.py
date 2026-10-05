"""Polling for one device."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from modbus_connection import ModbusError
from modbus_connection.model import Component

from .const import (
    CONF_BAUDRATE,
    CONF_BYTESIZE,
    CONF_PARITY,
    CONF_SERIAL_PORT,
    CONF_STOPBITS,
    CONF_TRANSPORT,
    DOMAIN,
)
from .library import ConnectionConfig, DeviceType, Transport

_LOGGER = logging.getLogger(__name__)

type PoolModbusConfigEntry = ConfigEntry[PoolModbusCoordinator]


def connection_config(data: Mapping[str, Any]) -> ConnectionConfig:
    """The connection settings stored in a config entry."""
    transport = Transport(data[CONF_TRANSPORT])
    if transport is Transport.SERIAL:
        return ConnectionConfig(
            transport,
            serial_port=data[CONF_SERIAL_PORT],
            baudrate=int(data[CONF_BAUDRATE]),
            bytesize=int(data[CONF_BYTESIZE]),
            parity=data[CONF_PARITY],
            stopbits=int(data[CONF_STOPBITS]),
        )
    return ConnectionConfig(transport, host=data[CONF_HOST], port=int(data[CONF_PORT]))


class PoolModbusCoordinator(DataUpdateCoordinator[None]):
    """Reads one device; entities take their values from its model."""

    config_entry: PoolModbusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PoolModbusConfigEntry,
        device_type: DeviceType,
        device: Component,
        interval: timedelta,
    ) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=entry.title, update_interval=interval
        )
        self.device_type = device_type
        self.device = device

    async def _async_update_data(self) -> None:
        try:
            await self.device.async_update()
        except (ModbusError, OSError, TimeoutError) as err:
            raise UpdateFailed(f"{self.name} did not answer: {err}") from err

    @property
    def device_info(self) -> DeviceInfo:
        device_type = self.device_type
        version = device_type.software_version
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.unique_id or self.config_entry.entry_id)},
            name=self.config_entry.title,
            manufacturer=device_type.manufacturer,
            model=device_type.models[0],
            sw_version=version(self.device) if version is not None else None,
        )
