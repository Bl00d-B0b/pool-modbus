"""Device lists in TOML, one ``[[device]]`` table per device.

[[device]]
type = "t010"
host = "192.168.1.50"   # transport defaults to tcp, port to 502
unit = 2
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .connection import ConnectionConfig, Transport
from .devices import get_device_type

_CONNECTION_KEYS = frozenset(
    {"transport", "host", "port", "serial_port", "baudrate", "bytesize", "parity", "stopbits"}
)
_DEVICE_KEYS = frozenset({"type", "unit", "name"}) | _CONNECTION_KEYS


@dataclass(frozen=True)
class DeviceEntry:
    """One device to read: its type, Modbus ID and connection settings."""

    type: str
    unit_id: int
    connection: ConnectionConfig
    name: str | None = None


def parse_device(raw: dict[str, Any], *, where: str = "device") -> DeviceEntry:
    """Build an entry from one ``[[device]]`` table.

    Raises ``ValueError`` for missing or unknown keys and invalid connection
    settings, and ``KeyError`` for an unknown device type.
    """
    unknown = set(raw) - _DEVICE_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
    if "type" not in raw:
        raise ValueError(f"{where}: missing type")
    device_type = get_device_type(raw["type"])
    settings = {key: raw[key] for key in _CONNECTION_KEYS if key in raw}
    if "transport" in settings:
        settings["transport"] = Transport(settings["transport"])
    connection = ConnectionConfig(**settings)
    connection.params()  # validate now rather than at connect time
    return DeviceEntry(
        type=device_type.key,
        unit_id=int(raw.get("unit", device_type.default_unit_id)),
        connection=connection,
        name=raw.get("name"),
    )


def load_devices(path: str | Path) -> list[DeviceEntry]:
    """Read a TOML device list.

    Raises ``ValueError`` for an invalid list (see ``parse_device``).
    """
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    tables = data.get("device", [])
    if not tables:
        raise ValueError(f"{path}: no [[device]] tables")
    return [parse_device(raw, where=f"device {i}") for i, raw in enumerate(tables, start=1)]
