"""Read a device from the command line. Read-only.

python -m pool_modbus types
python -m pool_modbus read emec_ld --host 192.168.1.50 --unit 3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from enum import Enum
from typing import Any

from modbus_connection.model import Component

from .connection import ConnectionConfig, Transport
from .devices import DEVICE_TYPES, get_device_type


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pool_modbus", description="Read pool equipment over Modbus (read-only)."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("types", help="list supported device types")
    read = commands.add_parser("read", help="read one device and print its values")
    read.add_argument("device_type", choices=sorted(DEVICE_TYPES))
    read.add_argument("--transport", choices=[t.value for t in Transport], default="tcp")
    read.add_argument("--host", help="gateway or device address (TCP, RTU over TCP, UDP)")
    read.add_argument("--port", type=int, default=502)
    read.add_argument("--serial-port", help="e.g. /dev/ttyUSB0 or COM3 (serial)")
    read.add_argument("--baudrate", type=int, default=9600)
    read.add_argument("--parity", choices=["N", "E", "O"], default="N")
    read.add_argument("--stopbits", type=int, choices=[1, 2], default=1)
    read.add_argument("--unit", type=int, help="Modbus ID; defaults to the device type's")
    read.add_argument("--timeout", type=float, default=3.0, help="seconds per request")
    return parser


def _values(device: Component) -> list[tuple[str, Any]]:
    names = list(type(device).declared_fields)
    names += [name for name, attr in vars(type(device)).items() if isinstance(attr, property)]
    return [(name, getattr(device, name)) for name in names]


async def _read(args: argparse.Namespace) -> int:
    try:
        from modbus_connection.pymodbus import ModbusConnection
    except ImportError:
        print(
            "the command-line tool needs a backend: pip install 'pool-modbus[cli]'", file=sys.stderr
        )
        return 2
    device_type = get_device_type(args.device_type)
    config = ConnectionConfig(
        transport=Transport(args.transport),
        host=args.host,
        port=args.port,
        serial_port=args.serial_port,
        baudrate=args.baudrate,
        parity=args.parity,
        stopbits=args.stopbits,
    )
    unit_id = args.unit if args.unit is not None else device_type.default_unit_id
    connection = ModbusConnection(config.params(), timeout=args.timeout)
    try:
        await connection.connect()
        device = device_type.model(connection.for_unit(unit_id))
        await device.async_update()
    finally:
        await connection.close()
    for name, value in _values(device):
        shown = value.name if isinstance(value, Enum) else value
        print(f"{name:22} {shown}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "types":
        for device_type in DEVICE_TYPES.values():
            maker, tested = device_type.manufacturer, ", ".join(device_type.models)
            print(f"{device_type.key:12} {device_type.name} ({maker}; tested: {tested})")
        return 0
    return asyncio.run(_read(args))


if __name__ == "__main__":
    raise SystemExit(main())
