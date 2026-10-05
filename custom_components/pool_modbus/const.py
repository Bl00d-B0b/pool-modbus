"""Constants for the Pool equipment integration."""

from typing import Final

DOMAIN: Final = "pool_modbus"

CONF_DEVICE_TYPE: Final = "device_type"
CONF_TRANSPORT: Final = "transport"
CONF_UNIT_ID: Final = "unit_id"
CONF_SERIAL_PORT: Final = "serial_port"
CONF_BAUDRATE: Final = "baudrate"
CONF_BYTESIZE: Final = "bytesize"
CONF_PARITY: Final = "parity"
CONF_STOPBITS: Final = "stopbits"

DEFAULT_PORT: Final = 502
DEFAULT_SCAN_INTERVAL: Final = 15
"""Seconds between reads of one device."""
