"""Connection settings for one device: transport and address.

Every device is configured on its own, so two devices can sit on different
gateways, ports or serial lines. Devices whose settings match share one
connection; Home Assistant's ``async_get_unit`` does that automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from modbus_connection import ModbusSerialParams, ModbusTcpParams, ModbusUdpParams

type ModbusParams = ModbusTcpParams | ModbusUdpParams | ModbusSerialParams


class Transport(StrEnum):
    """How a device is reached."""

    TCP = "tcp"
    """Modbus TCP, e.g. through a TCP-to-RS-485 gateway."""

    RTU_OVER_TCP = "rtu_over_tcp"
    """Raw RTU frames tunnelled through a TCP socket (transparent gateways)."""

    UDP = "udp"
    """Modbus UDP."""

    SERIAL = "serial"
    """An RS-485 or RS-232 adapter attached to this machine."""


@dataclass(frozen=True)
class ConnectionConfig:
    """Where one device is reached. Only the fields its transport uses matter."""

    transport: Transport = Transport.TCP
    host: str | None = None
    port: int = 502
    serial_port: str | None = None
    baudrate: int = 9600
    bytesize: Literal[7, 8] = 8
    parity: Literal["N", "E", "O"] = "N"
    stopbits: Literal[1, 2] = 1
    serial_framer: Literal["rtu", "ascii"] = "rtu"

    def params(self) -> ModbusParams:
        """The modbus-connection parameters for this transport.

        Raises ``ValueError`` when the transport's address is missing.
        """
        match self.transport:
            case Transport.TCP:
                return ModbusTcpParams(host=self._host(), port=self.port)
            case Transport.RTU_OVER_TCP:
                # RTU frames a serial line; modbus-connection models a tunnelled one as a
                # serial link on a socket.
                return ModbusSerialParams(
                    device=f"socket://{self._host()}:{self.port}", framer="rtu"
                )
            case Transport.UDP:
                return ModbusUdpParams(host=self._host(), port=self.port)
            case Transport.SERIAL:
                if not self.serial_port:
                    raise ValueError("serial needs serial_port, e.g. /dev/ttyUSB0 or COM3")
                return ModbusSerialParams(
                    device=self.serial_port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    framer=self.serial_framer,
                )
        raise ValueError(f"unknown transport {self.transport!r}")

    def _host(self) -> str:
        if not self.host:
            raise ValueError(f"{self.transport} needs a host")
        return self.host
