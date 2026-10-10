"""The EMEC LD's dosing settings from Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from modbus_connection import GatewayTargetError

from custom_components.pool_modbus.library.devices import writing

from .conftest import fake_unit
from .test_init import add, state

MODE = "select.dosing_pump_ph_dosing_mode"
PH_MAX = "number.dosing_pump_ph_max_value"
PH_MIN = "number.dosing_pump_ph_min_value"
MAX_RATE = "number.dosing_pump_ph_max_pulse_rate"
MIN_RATE = "number.dosing_pump_ph_min_pulse_rate"
SPEED = "number.dosing_pump_ph_pulse_speed"

# The snapshot (wire addresses): proportional mode (77 = 1), pH Max 10.00 (67),
# pH Min 7.50 (69), max rate 30 (71), min rate 0 (73), speed 0 (75).


@pytest.fixture(autouse=True)
def quick_confirm(monkeypatch):
    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0)


async def set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value}, True
    )


async def select(hass: HomeAssistant, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: MODE, ATTR_OPTION: option}, True
    )


async def test_dosing_settings_are_controls(hass: HomeAssistant) -> None:
    await add(hass, "emec_ld", fake_unit("emec_ld"))
    mode = hass.states.get(MODE)
    assert mode.state == "Proportional"
    assert mode.attributes["options"] == ["ON/OFF", "Proportional", "Disabled"]
    assert state(hass, PH_MAX) == "10.0"
    assert hass.states.get(PH_MAX).attributes["device_class"] == "ph"
    assert "unit_of_measurement" not in hass.states.get(PH_MAX).attributes
    assert hass.states.get(MAX_RATE).attributes["max"] == 180
    assert state(hass, SPEED) == STATE_UNAVAILABLE  # only used in ON/OFF mode
    assert mode.attributes["icon"] == "mdi:chart-line-variant"
    registry = er.async_get(hass)
    assert registry.async_get("sensor.dosing_pump_ph_probe_voltage").entity_category == "diagnostic"
    assert registry.async_get("sensor.dosing_pump_ph_level").entity_category is None


async def test_ph_values_and_rates(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    await add(hass, "emec_ld", unit)

    await set_number(hass, PH_MIN, 7.55)
    await set_number(hass, MAX_RATE, 29)

    assert unit.writes == [(69, 755), (71, 29)]
    assert state(hass, PH_MIN) == "7.55"
    assert state(hass, MAX_RATE) == "29"


async def test_a_write_answered_too_late_shows_at_once(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    await add(hass, "emec_ld", unit)
    unit.write_reply = GatewayTargetError()  # stored, but the gateway reports 0x0B
    unit.stale_reads = 2

    await set_number(hass, PH_MIN, 7.55)

    assert unit.writes == [(69, 755)]
    assert state(hass, PH_MIN) == "7.55"


async def test_a_min_rate_sets_the_max_rate_to_0(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    await add(hass, "emec_ld", unit)

    await set_number(hass, MIN_RATE, 5)

    assert unit.writes == [(73, 5), (71, 0)]
    assert (state(hass, MIN_RATE), state(hass, MAX_RATE)) == ("5", "0")


async def test_mode_switch(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    await add(hass, "emec_ld", unit)

    await select(hass, "ON/OFF")

    assert unit.writes == [(71, 100), (75, 1), (77, 0)]
    assert state(hass, MODE) == "ON/OFF"
    assert state(hass, SPEED) == "1"
    assert state(hass, MAX_RATE) == STATE_UNAVAILABLE  # proportional mode only


async def test_values_the_pump_does_not_take(hass: HomeAssistant) -> None:
    unit = fake_unit("emec_ld")
    await add(hass, "emec_ld", unit)

    with pytest.raises(ServiceValidationError):
        await set_number(hass, MIN_RATE, 2.5)

    assert unit.writes == []
