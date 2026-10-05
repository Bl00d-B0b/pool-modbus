"""Read devices from the command line. Read-only.

python -m pool_modbus types
python -m pool_modbus read t010 --host 192.168.1.50 --unit 2
python -m pool_modbus read-config devices.toml
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from .config import DeviceEntry, load_devices, parse_device
from .connection import ConnectionConfig, Transport
from .devices import DEVICE_TYPES
from .reader import Connection, read_devices, render


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pool_modbus", description="Read pool equipment over Modbus (read-only)."
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="seconds per request")
    parser.add_argument("--raw", action="store_true", help="print every register field")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("types", help="list supported device types")

    read = commands.add_parser("read", help="read one device")
    read.add_argument("device_type", choices=sorted(DEVICE_TYPES))
    read.add_argument("--transport", choices=[t.value for t in Transport], default="tcp")
    read.add_argument("--host", help="gateway or device address (TCP, RTU over TCP, UDP)")
    read.add_argument("--port", type=int, default=502)
    read.add_argument("--serial-port", help="e.g. /dev/ttyUSB0 or COM3 (serial)")
    read.add_argument("--baudrate", type=int, default=9600)
    read.add_argument("--parity", choices=["N", "E", "O"], default="N")
    read.add_argument("--stopbits", type=int, choices=[1, 2], default=1)
    read.add_argument("--unit", type=int, help="Modbus ID; defaults to the device type's")

    config = commands.add_parser("read-config", help="read every device in a TOML device list")
    config.add_argument("path", help="TOML file with one [[device]] table per device")
    return parser


def _entry_from_args(args: argparse.Namespace) -> DeviceEntry:
    raw = {
        "type": args.device_type,
        "transport": args.transport,
        "port": args.port,
        "baudrate": args.baudrate,
        "parity": args.parity,
        "stopbits": args.stopbits,
    }
    if args.host:
        raw["host"] = args.host
    if args.serial_port:
        raw["serial_port"] = args.serial_port
    if args.unit is not None:
        raw["unit"] = args.unit
    return parse_device(raw)


async def _run(entries: list[DeviceEntry], timeout: float, raw: bool) -> int:
    try:
        from modbus_connection.pymodbus import ModbusConnection
    except ImportError:
        print(
            "the command-line tool needs a backend: pip install 'pool-modbus[cli]'", file=sys.stderr
        )
        return 2

    async def connect(config: ConnectionConfig) -> Connection:
        connection = ModbusConnection(config.params(), timeout=timeout)
        await connection.connect()
        return connection

    results = await read_devices(entries, connect)
    print(render(results, raw=raw), end="")
    return 0 if all(result.device is not None for result in results) else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "types":
        for device_type in DEVICE_TYPES.values():
            maker, tested = device_type.manufacturer, ", ".join(device_type.models)
            print(f"{device_type.key:16} {device_type.name} ({maker}; tested: {tested})")
        return 0
    try:
        entries = (
            load_devices(args.path) if args.command == "read-config" else [_entry_from_args(args)]
        )
    except (ValueError, KeyError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    return asyncio.run(_run(entries, args.timeout, args.raw))


if __name__ == "__main__":
    raise SystemExit(main())
