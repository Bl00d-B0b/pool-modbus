"""Connection settings map onto modbus-connection parameters."""

from __future__ import annotations

import pytest
from modbus_connection import ModbusSerialParams, ModbusTcpParams, ModbusUdpParams

from pool_modbus import ConnectionConfig, Transport


def test_tcp() -> None:
    params = ConnectionConfig(host="192.168.1.50", port=5020).params()
    assert isinstance(params, ModbusTcpParams)
    assert (params.host, params.port, params.framer) == ("192.168.1.50", 5020, "socket")


def test_rtu_over_tcp_is_a_serial_link_on_a_socket() -> None:
    params = ConnectionConfig(Transport.RTU_OVER_TCP, host="192.168.1.50").params()
    assert isinstance(params, ModbusSerialParams)
    assert (params.device, params.framer) == ("socket://192.168.1.50:502", "rtu")


def test_udp() -> None:
    params = ConnectionConfig(Transport.UDP, host="192.168.1.50").params()
    assert isinstance(params, ModbusUdpParams)
    assert params.port == 502


def test_serial() -> None:
    config = ConnectionConfig(
        Transport.SERIAL, serial_port="/dev/ttyUSB0", baudrate=38400, parity="E", stopbits=1
    )
    params = config.params()
    assert isinstance(params, ModbusSerialParams)
    assert (params.device, params.baudrate, params.parity, params.framer) == (
        "/dev/ttyUSB0",
        38400,
        "E",
        "rtu",
    )


@pytest.mark.parametrize("transport", [Transport.TCP, Transport.RTU_OVER_TCP, Transport.UDP])
def test_network_transport_needs_a_host(transport: Transport) -> None:
    with pytest.raises(ValueError, match="needs a host"):
        ConnectionConfig(transport).params()


def test_serial_needs_a_port() -> None:
    with pytest.raises(ValueError, match="serial_port"):
        ConnectionConfig(Transport.SERIAL).params()
