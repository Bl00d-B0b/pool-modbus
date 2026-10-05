"""Every device type's user-facing values work on data from real hardware."""

from __future__ import annotations

import pytest

from pool_modbus import DEVICE_TYPES
from pool_modbus.reader import format_value


@pytest.fixture
def snapshot_units(make_unit, make_pump, controller_snapshot, t010_snapshot, ldphcl_snapshot):
    return {
        "pool_controller": make_unit(controller_snapshot),
        "t010": make_unit(t010_snapshot),
        "emec_ld": make_pump(ldphcl_snapshot),
    }


def test_every_device_type_has_a_snapshot(snapshot_units) -> None:
    assert set(snapshot_units) == set(DEVICE_TYPES)


@pytest.mark.parametrize("key", sorted(DEVICE_TYPES))
async def test_every_value_is_known(snapshot_units, key: str) -> None:
    device_type = DEVICE_TYPES[key]
    device = device_type.model(snapshot_units[key])
    await device.async_update()
    unknown = [v.name for v in device_type.values if format_value(v, v.get(device)) == "unknown"]
    assert unknown == []


@pytest.mark.parametrize("key", sorted(DEVICE_TYPES))
def test_value_keys_and_names_are_unique(key: str) -> None:
    device_type = DEVICE_TYPES[key]
    values = device_type.values
    thermostat = [device_type.thermostat] if device_type.thermostat else []
    assert len({v.key for v in [*values, *thermostat]}) == len(values) + len(thermostat)
    assert len({v.name for v in [*values, *thermostat]}) == len(values) + len(thermostat)


@pytest.mark.parametrize("key", sorted(DEVICE_TYPES))
def test_writable_numbers_declare_their_range(key: str) -> None:
    for value in DEVICE_TYPES[key].values:
        if value.writable and not value.binary and value.options is None:
            assert None not in (value.minimum, value.maximum, value.step), value.name
            assert value.minimum < value.maximum, value.name
        assert value.number_mode in (None, "box", "slider"), value.name
        if value.number_mode is not None:
            assert value.writable and not value.binary, value.name
