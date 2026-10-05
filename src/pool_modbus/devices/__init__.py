"""Supported device types, one module each.

To add a device type, write a module in this package that defines a
``DEVICE_TYPE`` and list it in ``_TYPES``. See CONTRIBUTING.md.
"""

from __future__ import annotations

from . import emec_ld
from .base import DeviceType, Variant

_TYPES: tuple[DeviceType, ...] = (emec_ld.DEVICE_TYPE,)

DEVICE_TYPES: dict[str, DeviceType] = {device_type.key: device_type for device_type in _TYPES}


def get_device_type(key: str) -> DeviceType:
    """The device type registered under ``key``.

    Raises ``KeyError`` for an unknown key.
    """
    try:
        return DEVICE_TYPES[key]
    except KeyError:
        known = ", ".join(sorted(DEVICE_TYPES))
        raise KeyError(f"unknown device type {key!r}; known: {known}") from None


__all__ = ["DEVICE_TYPES", "DeviceType", "Variant", "get_device_type"]
