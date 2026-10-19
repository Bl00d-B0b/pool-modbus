"""Modbus device models for pool equipment."""

from .connection import ConnectionConfig, Transport, describe_connection
from .devices import (
    DEVICE_TYPES,
    SCAN_GROUPS,
    Action,
    Cover,
    DeviceType,
    Feature,
    ImplausibleReadError,
    ScanGroup,
    Thermostat,
    Value,
    Variant,
    get_device_type,
)
from .devices.reading import update_or_keep
from .devices.scan import group_model, read_plan

__version__ = "2026.10.0"

__all__ = [
    "DEVICE_TYPES",
    "SCAN_GROUPS",
    "Action",
    "ConnectionConfig",
    "Cover",
    "DeviceType",
    "Feature",
    "ImplausibleReadError",
    "ScanGroup",
    "Thermostat",
    "Transport",
    "Value",
    "Variant",
    "__version__",
    "describe_connection",
    "get_device_type",
    "group_model",
    "read_plan",
    "update_or_keep",
]
