"""The device-type registry."""

from __future__ import annotations

import pytest

from pool_modbus import DEVICE_TYPES, get_device_type


def test_all_device_types_are_registered() -> None:
    assert list(DEVICE_TYPES) == ["pool_controller", "t010", "emec_ld"]


def test_manufacturers() -> None:
    assert DEVICE_TYPES["t010"].manufacturer == "Optika ir technologija"
    assert DEVICE_TYPES["emec_ld"].manufacturer == "EMEC"


def test_emec_ld_is_registered() -> None:
    device_type = DEVICE_TYPES["emec_ld"]
    assert device_type.manufacturer == "EMEC"
    assert "LDPHCL" in device_type.models


def test_unknown_device_type() -> None:
    with pytest.raises(KeyError, match="emec_ld"):
        get_device_type("no_such_device")


def test_unknown_variant(make_pump, ldphcl_snapshot) -> None:
    with pytest.raises(ValueError, match="unknown variant"):
        get_device_type("emec_ld").model(make_pump(ldphcl_snapshot), variant="nope")
