"""Modbus device models for pool equipment."""

from .config import DeviceEntry, load_devices
from .connection import ConnectionConfig, Transport
from .devices import (
    DEVICE_TYPES,
    SCAN_GROUPS,
    Action,
    Cover,
    DeviceType,
    Feature,
    ScanGroup,
    Thermostat,
    Value,
    Variant,
    get_device_type,
)
from .devices.scan import group_model, read_plan
from .reader import DeviceResult, read_devices

__version__ = "2026.10.0"

__all__ = [
    "DEVICE_TYPES",
    "SCAN_GROUPS",
    "Action",
    "ConnectionConfig",
    "Cover",
    "DeviceEntry",
    "DeviceResult",
    "DeviceType",
    "Feature",
    "ScanGroup",
    "Thermostat",
    "Transport",
    "Value",
    "Variant",
    "__version__",
    "get_device_type",
    "group_model",
    "load_devices",
    "read_devices",
    "read_plan",
]
