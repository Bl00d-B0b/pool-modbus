"""Changing T010 settings from Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.number import (
    ATTR_VALUE,
    SERVICE_SET_VALUE,
)
from homeassistant.components.number import (
    DOMAIN as NUMBER_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import fake_unit
from .test_init import add, state

CLIMATE = "climate.pool_thermostat_pool_thermostat"
DELAYING = "switch.pool_thermostat_pool_delaying"
OFFSET = "number.pool_thermostat_offset_temperature"
DELAY = "number.pool_thermostat_set_delay_time"
POWER = "sensor.pool_thermostat_heating_power"

# The snapshot: setpoint 20.0 °C (register 5 = 200), heating blocked (register 8 = 0x0100).


async def test_settings_are_config_entities(hass: HomeAssistant) -> None:
    await add(hass, "t010", fake_unit("t010"))
    registry = er.async_get(hass)
    assert registry.async_get(OFFSET).entity_category == "config"
    assert registry.async_get(DELAYING).entity_category is None
    assert hass.states.get(OFFSET).attributes["min"] == -3.1
    assert hass.states.get(OFFSET).attributes["mode"] == "box"
    assert hass.states.get(DELAY).attributes["mode"] == "box"


async def test_the_thermostat_is_not_repeated_by_other_entities(hass: HomeAssistant) -> None:
    await add(hass, "t010", fake_unit("t010"))
    registry = er.async_get(hass)
    for entity_id in (
        "switch.pool_thermostat_pool_heating",
        "number.pool_thermostat_set_temperature",
        "sensor.pool_thermostat_heating_mode",
    ):
        assert registry.async_get(entity_id) is None, entity_id
    # Heating power adds to the thermostat in PWM mode only, so it starts disabled.
    assert registry.async_get(POWER).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(POWER) is None


async def test_delaying_switch_keeps_the_heating_bit(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    await add(hass, "t010", unit)

    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: DELAYING}, blocking=True
    )

    assert unit.writes == [(8, 0x0300)]


@pytest.mark.parametrize(
    ("entity_id", "value", "write"),
    [(OFFSET, -0.5, (6, 0xFFFB)), (DELAY, 10, (7, 10))],
)
async def test_number(hass: HomeAssistant, entity_id: str, value: float, write) -> None:
    unit = fake_unit("t010")
    await add(hass, "t010", unit)

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value},
        blocking=True,
    )

    assert unit.writes == [write]
    assert float(state(hass, entity_id)) == pytest.approx(value)


async def test_setting_the_held_value_does_not_write(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    await add(hass, "t010", unit)

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 20.0},
        blocking=True,
    )

    assert unit.writes == []


async def test_thermostat(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    await add(hass, "t010", unit)
    current = hass.states.get(CLIMATE)
    assert current.attributes[ATTR_CURRENT_TEMPERATURE] == 19.0
    assert current.attributes[ATTR_TEMPERATURE] == 20.0
    assert current.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_TEMPERATURE: 32.5},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.HEAT},
        blocking=True,
    )

    assert unit.writes == [(5, 325), (8, 0x0000)]
    current = hass.states.get(CLIMATE)
    assert current.state == HVACMode.HEAT
    assert current.attributes[ATTR_TEMPERATURE] == 32.5
    assert current.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE


async def test_thermostat_turn_off(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    unit.registers[8] = 0x0000  # heating allowed
    await add(hass, "t010", unit)
    assert state(hass, CLIMATE) == HVACMode.HEAT

    await hass.services.async_call(
        CLIMATE_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: CLIMATE}, blocking=True
    )

    assert unit.writes == [(8, 0x0100)]
    assert state(hass, CLIMATE) == HVACMode.OFF


async def test_device_failure_is_reported(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    await add(hass, "t010", unit)
    unit.fail = TimeoutError("no answer")

    with pytest.raises(HomeAssistantError, match="did not accept"):
        await hass.services.async_call(
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: CLIMATE, ATTR_HVAC_MODE: HVACMode.HEAT},
            blocking=True,
        )


async def test_value_the_firmware_refuses(hass: HomeAssistant) -> None:
    unit = fake_unit("t010")
    await add(hass, "t010", unit)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            NUMBER_DOMAIN,
            SERVICE_SET_VALUE,
            {ATTR_ENTITY_ID: DELAY, ATTR_VALUE: 2.5},  # whole minutes only
            blocking=True,
        )

    assert unit.writes == []


async def test_read_only_devices_get_no_controls(hass: HomeAssistant) -> None:
    await add(hass, "emec_ld", fake_unit("emec_ld"))
    assert hass.states.async_entity_ids(SWITCH_DOMAIN) == []
    assert hass.states.async_entity_ids(NUMBER_DOMAIN) == []
    assert hass.states.async_entity_ids(CLIMATE_DOMAIN) == []


async def test_entity_shows_the_new_value_behind_a_caching_gateway(
    hass: HomeAssistant, monkeypatch
) -> None:
    from custom_components.pool_modbus.library.devices import writing

    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0.0)
    unit = fake_unit("t010")
    await add(hass, "t010", unit)
    unit.stale_reads = 3  # reads lag the write, as on the test installation

    await hass.services.async_call(
        NUMBER_DOMAIN,
        SERVICE_SET_VALUE,
        {ATTR_ENTITY_ID: DELAY, ATTR_VALUE: 4},
        blocking=True,
    )

    assert unit.writes == [(7, 4)]
    assert state(hass, DELAY) == "4"
