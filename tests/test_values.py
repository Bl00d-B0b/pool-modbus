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
    values = DEVICE_TYPES[key].values
    assert len({v.key for v in values}) == len(values)
    assert len({v.name for v in values}) == len(values)
