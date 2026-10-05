"""Modbus device models for pool equipment."""

from .config import DeviceEntry, load_devices
from .connection import ConnectionConfig, Transport
from .devices import DEVICE_TYPES, DeviceType, Value, Variant, get_device_type
from .reader import DeviceResult, read_devices

__version__ = "0.2.0"

__all__ = [
    "DEVICE_TYPES",
    "ConnectionConfig",
    "DeviceEntry",
    "DeviceResult",
    "DeviceType",
    "Transport",
    "Value",
    "Variant",
    "__version__",
    "get_device_type",
    "load_devices",
    "read_devices",
]
