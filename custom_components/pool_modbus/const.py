"""Constants for the Pool equipment integration."""

from typing import Final

from homeassistant.const import CONF_SCAN_INTERVAL

DOMAIN: Final = "pool_modbus"

CONF_DEVICE_TYPE: Final = "device_type"
CONF_TRANSPORT: Final = "transport"
CONF_UNIT_ID: Final = "unit_id"
CONF_SERIAL_PORT: Final = "serial_port"
CONF_BAUDRATE: Final = "baudrate"
CONF_BYTESIZE: Final = "bytesize"
CONF_PARITY: Final = "parity"
CONF_STOPBITS: Final = "stopbits"

CONF_SCAN_INTERVAL_MEDIUM: Final = "scan_interval_medium"
CONF_SCAN_INTERVAL_FAST: Final = "scan_interval_fast"
SCAN_INTERVAL_KEYS: Final = {
    "fast": CONF_SCAN_INTERVAL_FAST,
    "medium": CONF_SCAN_INTERVAL_MEDIUM,
    "slow": CONF_SCAN_INTERVAL,  # the key older entries already use
}
DEFAULT_SCAN_INTERVALS: Final = {"fast": 5, "medium": 10, "slow": 15}
"""Seconds between reads of each scan group, as in solax-modbus."""

FEATURE_PREFIX: Final = "read_"
"""Options key of a device type's optional part: ``read_<feature key>``."""

DEFAULT_PORT: Final = 502
