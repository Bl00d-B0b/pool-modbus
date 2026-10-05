"""TOML device lists."""

from __future__ import annotations

from pathlib import Path

import pytest

from pool_modbus import Transport, load_devices

EXAMPLE = Path(__file__).parent.parent / "examples" / "devices.toml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "devices.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_three_devices_on_one_gateway_and_one_serial(tmp_path: Path) -> None:
    entries = load_devices(
        write(
            tmp_path,
            """
[[device]]
type = "pool_controller"
host = "192.168.1.50"
unit = 1

[[device]]
type = "t010"
host = "192.168.1.50"
unit = 2
name = "Thermostat"

[[device]]
type = "emec_ld"
transport = "serial"
serial_port = "/dev/ttyUSB0"
baudrate = 38400
unit = 3
""",
        )
    )
    assert [entry.type for entry in entries] == ["pool_controller", "t010", "emec_ld"]
    assert [entry.unit_id for entry in entries] == [1, 2, 3]
    assert entries[0].connection == entries[1].connection
    assert entries[1].name == "Thermostat"
    assert entries[2].connection.transport is Transport.SERIAL
    assert entries[2].connection.baudrate == 38400


def test_unit_defaults_to_the_device_types(tmp_path: Path) -> None:
    (entry,) = load_devices(write(tmp_path, '[[device]]\ntype = "t010"\nhost = "10.0.0.5"\n'))
    assert entry.unit_id == 1


@pytest.mark.parametrize(
    ("text", "error", "match"),
    [
        ('[[device]]\nhost = "10.0.0.5"\n', ValueError, "missing type"),
        (
            '[[device]]\ntype = "t010"\nhost = "10.0.0.5"\ncolour = "red"\n',
            ValueError,
            "unknown keys",
        ),
        ('[[device]]\ntype = "t010"\n', ValueError, "needs a host"),
        ('[[device]]\ntype = "heat_pump"\nhost = "10.0.0.5"\n', KeyError, "unknown device type"),
        ("# nothing here\n", ValueError, "no \\[\\[device\\]\\] tables"),
    ],
)
def test_invalid_lists(tmp_path: Path, text: str, error: type[Exception], match: str) -> None:
    with pytest.raises(error, match=match):
        load_devices(write(tmp_path, text))


def test_example_file_is_valid() -> None:
    entries = load_devices(EXAMPLE)
    assert {entry.type for entry in entries} == {"pool_controller", "t010", "emec_ld"}
