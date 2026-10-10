"""Read several devices, sharing a connection between devices with the same settings."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Any, Protocol

from modbus_connection import ModbusError, ModbusUnit
from modbus_connection.model import Component

from .config import DeviceEntry
from .connection import ConnectionConfig, Transport
from .devices import DeviceType, Thermostat, Value, get_device_type


class Connection(Protocol):
    def for_unit(self, unit_id: int) -> ModbusUnit: ...

    async def close(self) -> None: ...


type Connect = Callable[[ConnectionConfig], Awaitable[Connection]]


@dataclass(frozen=True)
class DeviceResult:
    """One device after a read: its model, or why it could not be read."""

    entry: DeviceEntry
    device: Component | None
    error: str | None = None


async def read_devices(entries: list[DeviceEntry], connect: Connect) -> list[DeviceResult]:
    """Read every entry, in order, opening one connection per distinct setting.

    A device that fails is reported as unavailable; the others are still read.
    """
    results: dict[int, DeviceResult] = {}
    groups: dict[ConnectionConfig, list[int]] = {}
    for index, entry in enumerate(entries):
        groups.setdefault(entry.connection, []).append(index)
    for config, indices in groups.items():
        try:
            connection = await connect(config)
        except (ModbusError, OSError, TimeoutError) as err:
            for index in indices:
                results[index] = DeviceResult(entries[index], None, f"connection failed: {err}")
            continue
        try:
            for index in indices:
                entry = entries[index]
                device = get_device_type(entry.type).model(connection.for_unit(entry.unit_id))
                try:
                    await device.async_update()
                except (ModbusError, OSError, TimeoutError) as err:
                    results[index] = DeviceResult(entry, None, str(err) or type(err).__name__)
                else:
                    results[index] = DeviceResult(entry, device)
        finally:
            await connection.close()
    return [results[index] for index in range(len(entries))]


def format_value(value: Value, raw: Any) -> str:
    """A value as text, the way Home Assistant would show it."""
    if raw is None:
        return "unknown"
    if value.binary or isinstance(raw, bool):
        return "on" if raw else "off"
    if isinstance(raw, Enum):
        text = getattr(raw, "label", raw.name)
    elif isinstance(raw, float):
        text = str(round(raw, 2))
    elif isinstance(raw, datetime):
        text = raw.isoformat(sep=" ")
    elif isinstance(raw, time):
        text = raw.strftime("%H:%M")
    else:
        text = str(raw)
    return f"{text} {value.unit}" if value.unit else text


def describe_connection(config: ConnectionConfig) -> str:
    if config.transport is Transport.SERIAL:
        framing = f"{config.bytesize}{config.parity}{config.stopbits}"
        return f"serial {config.serial_port} {config.baudrate} {framing}"
    return f"{config.transport} {config.host}:{config.port}"


def render(results: list[DeviceResult], *, raw: bool = False) -> str:
    """Text for the command line: each device's values, or the reason it is unavailable."""
    lines: list[str] = []
    for result in results:
        entry = result.entry
        device_type = get_device_type(entry.type)
        title = entry.name or device_type.name
        lines.append(f"{title} (unit {entry.unit_id}, {describe_connection(entry.connection)})")
        if result.device is None:
            lines.append(f"  unavailable: {result.error}")
        else:
            rows = _raw_rows(result.device) if raw else _value_rows(device_type, result.device)
            width = max(len(name) for name, _ in rows)
            lines += [f"  {name:<{width}}  {text}" for name, text in rows]
        lines.append("")
    return "\n".join(lines)


def value_text(value: Value, device: Component) -> str:
    """One value of an updated device as text; "unavailable" where it does not apply."""
    if value.available is not None and not value.available(device):
        return "unavailable"
    return format_value(value, value.get(device))


def thermostat_text(thermostat: Thermostat, device: Component) -> str:
    """The thermostat as one line, e.g. "heat, heating at 100 %, 19.0 °C, target 32.5 °C"."""
    mode = thermostat.mode(device)
    if mode is None:
        return "unknown"
    parts = [mode]
    action = thermostat.action(device)
    if mode != "off" and action is not None:
        power = (thermostat.attributes(device) if thermostat.attributes else {}).get(
            "heating_power"
        )
        parts.append(f"{action} at {power} %" if action == "heating" and power else action)
    current = thermostat.current_temperature(device)
    if current is not None:
        parts.append(f"{current} °C")
    target = thermostat.target_temperature(device)
    if target is not None:
        parts.append(f"target {target} °C")
    return ", ".join(parts)


def _value_rows(device_type: DeviceType, device: Component) -> list[tuple[str, str]]:
    rows = [(value.name, value_text(value, device)) for value in device_type.values]
    thermostat = device_type.thermostat
    if thermostat is not None:
        rows.insert(0, (thermostat.name, thermostat_text(thermostat, device)))
    cover = device_type.cover
    if cover is not None:
        closed = cover.is_closed(device)
        text = "unknown" if closed is None else ("closed" if closed else "open")
        rows.append((cover.name, text))
    return rows


def _raw_rows(device: Component) -> list[tuple[str, str]]:
    names = list(type(device).declared_fields)
    rows = []
    for name in names:
        value = getattr(device, name)
        rows.append((name, value.name if isinstance(value, Enum) else str(value)))
    return rows
