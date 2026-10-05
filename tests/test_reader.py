"""Reading several devices: shared connections, failures, and the printed output."""

from __future__ import annotations

from datetime import datetime

import pytest

from pool_modbus import ConnectionConfig, DeviceEntry, read_devices
from pool_modbus.devices import Value
from pool_modbus.devices.emec_ld import PulseMode
from pool_modbus.reader import format_value, render

GATEWAY = ConnectionConfig(host="192.168.1.50")
OTHER_GATEWAY = ConnectionConfig(host="192.168.1.51")


class FakeConnection:
    def __init__(self, units) -> None:
        self.units = units
        self.closed = False

    def for_unit(self, unit_id: int):
        return self.units[unit_id]

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def bus(make_unit, make_pump, controller_snapshot, t010_snapshot, ldphcl_snapshot):
    """Three devices behind one gateway, as in the test installation."""
    return {
        1: make_unit(controller_snapshot),
        2: make_unit(t010_snapshot),
        3: make_pump(ldphcl_snapshot),
    }


def entries(t010_connection: ConnectionConfig = GATEWAY) -> list[DeviceEntry]:
    return [
        DeviceEntry("pool_controller", 1, GATEWAY),
        DeviceEntry("t010", 2, t010_connection),
        DeviceEntry("emec_ld", 3, GATEWAY),
    ]


def recording_connect(bus, opened: list):
    async def connect(config: ConnectionConfig) -> FakeConnection:
        connection = FakeConnection(bus)
        opened.append((config, connection))
        return connection

    return connect


async def test_devices_on_one_gateway_share_a_connection(bus) -> None:
    opened: list = []
    results = await read_devices(entries(), recording_connect(bus, opened))

    assert [result.error for result in results] == [None, None, None]
    assert len(opened) == 1
    assert opened[0][1].closed
    assert results[0].device.filtration_mode == "Filtering"
    assert results[1].device.temperature == pytest.approx(19.0)
    assert results[2].device.ch1_value == pytest.approx(7.51)


async def test_each_connection_setting_gets_its_own_connection(bus) -> None:
    opened: list = []
    results = await read_devices(entries(OTHER_GATEWAY), recording_connect(bus, opened))

    assert [config.host for config, _ in opened] == ["192.168.1.50", "192.168.1.51"]
    assert [result.entry.type for result in results] == ["pool_controller", "t010", "emec_ld"]


async def test_a_failing_device_does_not_stop_the_others(bus, make_unit, t010_snapshot) -> None:
    bus[2] = make_unit(t010_snapshot, fail=TimeoutError("no answer"))
    results = await read_devices(entries(), recording_connect(bus, []))

    assert results[0].device is not None
    assert results[1].device is None
    assert "no answer" in results[1].error
    assert results[2].device is not None


async def test_unreachable_gateway_marks_its_devices_unavailable() -> None:
    async def connect(config: ConnectionConfig):
        raise OSError("gateway unreachable")

    results = await read_devices(entries(), connect)
    assert all(result.device is None for result in results)
    assert all("connection failed" in result.error for result in results)


async def test_output_uses_home_assistant_names(bus) -> None:
    text = render(await read_devices(entries(), recording_connect(bus, [])))
    lines = {" ".join(line.split()) for line in text.splitlines()}  # ignore column padding

    for expected in (
        "Pool controller (unit 1, tcp 192.168.1.50:502)",
        "Filtration Mode Filtering",
        "Saved Backwash Schedule Friday 06:00",
        "Pool Thermostat off",
        "Pool Temperature 19.0 °C",
        "Thermostat Firmware 1.7",
        "Pool pH Level 7.51 pH",
        "Out Relay pH On",
        "Ch1 pH pulse1 Mode Proportional",
    ):
        assert expected in lines


@pytest.mark.parametrize(
    ("value", "raw", "text"),
    [
        (Value("x", "X", lambda d: None, binary=True), True, "on"),
        (Value("x", "X", lambda d: None, binary=True), False, "off"),
        (Value("x", "X", lambda d: None, "pH"), 7.5, "7.5 pH"),
        (Value("x", "X", lambda d: None, "ppm"), 0.4567, "0.46 ppm"),
        (Value("x", "X", lambda d: None), PulseMode.ON_OFF, "ON/OFF"),
        (Value("x", "X", lambda d: None), datetime(2026, 10, 5, 12, 16, 3), "2026-10-05 12:16:03"),
        (Value("x", "X", lambda d: None, "°C"), None, "unknown"),
    ],
)
def test_format_value(value: Value, raw, text: str) -> None:
    assert format_value(value, raw) == text
