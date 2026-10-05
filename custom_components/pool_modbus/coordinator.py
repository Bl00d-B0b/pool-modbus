"""Polling for one device: one coordinator per scan group."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
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
    DEFAULT_SCAN_INTERVALS,
    DOMAIN,
    FEATURE_PREFIX,
    SCAN_INTERVAL_KEYS,
)
from .library import Action, ConnectionConfig, DeviceType, ScanGroup, Transport, Value

_LOGGER = logging.getLogger(__name__)


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


def scan_interval(options: Mapping[str, Any], group: ScanGroup) -> int:
    """Seconds between reads of ``group``."""
    return int(options.get(SCAN_INTERVAL_KEYS[group], DEFAULT_SCAN_INTERVALS[group]))


def enabled_features(device_type: DeviceType, options: Mapping[str, Any]) -> frozenset[str]:
    """The device type's optional parts switched on for this device."""
    return frozenset(
        feature.key
        for feature in device_type.features
        if options.get(FEATURE_PREFIX + feature.key, feature.default)
    )


class PoolModbusCoordinator(DataUpdateCoordinator[None]):
    """Reads one scan group of one device; its entities take their values from its model."""

    config_entry: PoolModbusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PoolModbusConfigEntry,
        device: Component,
        group: ScanGroup,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} ({group})",
            update_interval=timedelta(seconds=scan_interval(entry.options, group)),
        )
        self.device = device
        self.group = group

    async def _async_update_data(self) -> None:
        try:
            await self.device.async_update()
        except (ModbusError, OSError, TimeoutError) as err:
            raise UpdateFailed(f"{self.name} did not answer: {err}") from err


@dataclass
class PoolModbusData:
    """One device: its type, what is switched on, and a coordinator per scan group."""

    device_type: DeviceType
    features: frozenset[str]
    coordinators: dict[ScanGroup, PoolModbusCoordinator]
    device_info: DeviceInfo
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def values(self) -> tuple[Value, ...]:
        return self.device_type.enabled_values(self.features)

    @property
    def actions(self) -> tuple[Action, ...]:
        return self.device_type.enabled_actions(self.features)

    async def async_refresh_all(self) -> None:
        """Read every group now, e.g. after a write."""
        for coordinator in self.coordinators.values():
            await coordinator.async_refresh()


type PoolModbusConfigEntry = ConfigEntry[PoolModbusData]


def device_info(entry: ConfigEntry, device_type: DeviceType, device: Component) -> DeviceInfo:
    version = device_type.software_version
    return DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        name=entry.title,
        manufacturer=device_type.manufacturer,
        model=device_type.models[0],
        sw_version=version(device) if version is not None else None,
    )
