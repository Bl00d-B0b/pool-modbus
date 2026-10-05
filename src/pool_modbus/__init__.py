"""Modbus device models for pool equipment."""

from .connection import ConnectionConfig, Transport
from .devices import DEVICE_TYPES, DeviceType, Variant, get_device_type

__version__ = "0.1.0"

__all__ = [
    "DEVICE_TYPES",
    "ConnectionConfig",
    "DeviceType",
    "Transport",
    "Variant",
    "__version__",
    "get_device_type",
]
