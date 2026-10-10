"""The pool controller from Home Assistant: switches, cover, light, schedule, buttons."""

from __future__ import annotations

from datetime import datetime

import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.components.cover import (
    DOMAIN as COVER_DOMAIN,
)
from homeassistant.components.cover import (
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
)
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.select import (
    ATTR_OPTION,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import (
    DOMAIN as SELECT_DOMAIN,
)
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.components.time import ATTR_TIME, SERVICE_SET_VALUE
from homeassistant.components.time import DOMAIN as TIME_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.pool_modbus.library.devices import pool_controller, writing

from .conftest import fake_unit
from .test_init import add, state

FILTRATION = "switch.pool_controller_filtration"
COVER = "cover.pool_controller_cover"
LIGHT = "light.pool_controller_light"
DAY = "select.pool_controller_backwash_day"
TIME = "time.pool_controller_backwash_time"
BACKWASH = "button.pool_controller_start_backwash"
RESET = "button.pool_controller_reset_alarms"
SYNC = "button.pool_controller_sync_rtc"
MODE = "sensor.pool_controller_filtration_mode"

# The snapshot: register 16 = 1 (filtration on), backwash Friday 06:00 written and
# saved, status 24 = 0x0103 (pump running, filtering, level normal, cover closed).


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(pool_controller, "LONG_PULSE", 0)
    monkeypatch.setattr(pool_controller, "SHORT_PULSE", 0)
    monkeypatch.setattr(writing, "CONFIRM_INTERVAL", 0)


async def call(hass: HomeAssistant, domain: str, service: str, entity_id: str, **data) -> None:
    await hass.services.async_call(
        domain, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_entities_icons_and_classes(hass: HomeAssistant) -> None:
    await add(hass, "pool_controller", fake_unit("pool_controller"))
    assert state(hass, FILTRATION) == "on"
    assert state(hass, COVER) == "closed"
    assert state(hass, LIGHT) == "off"
    assert state(hass, DAY) == "Friday"
    assert state(hass, TIME) == "06:00:00"
    mode = hass.states.get(MODE)
    assert (mode.state, mode.attributes["icon"]) == ("Filtering", "mdi:air-filter")
    assert mode.attributes["device_class"] == "enum"
    assert hass.states.get(COVER).attributes["device_class"] == "gate"
    assert hass.states.get(COVER).attributes["icon"] == "mdi:gate"
    assert hass.states.get(LIGHT).attributes["icon"] == "mdi:lightbulb-off"
    assert len(hass.states.get(DAY).attributes["options"]) == 8  # Off and the weekdays
    # The cover and light replace the old open/close buttons and status sensors.
    registry = er.async_get(hass)
    for gone in (
        "binary_sensor.pool_controller_pool_light",
        "sensor.pool_controller_pool_cover",
        "button.pool_controller_open_pool",
        "select.pool_controller_backwash_time",  # now a time entity
    ):
        assert registry.async_get(gone) is None, gone


async def test_filtration_switch(hass: HomeAssistant) -> None:
    unit = fake_unit("pool_controller")
    await add(hass, "pool_controller", unit)

    await call(hass, SWITCH_DOMAIN, SERVICE_TURN_OFF, FILTRATION)

    assert unit.writes == [(16, 0x0000)]
    assert state(hass, FILTRATION) == "off"


async def test_cover_open_and_close(hass: HomeAssistant) -> None:
    unit = fake_unit("pool_controller")
    await add(hass, "pool_controller", unit)

    await call(hass, COVER_DOMAIN, SERVICE_OPEN_COVER, COVER)
    assert unit.writes == [(16, 0x0005), (16, 0x0001)]

    unit.registers[24] |= 1 << 3  # the cover reached open
    await call(hass, COVER_DOMAIN, SERVICE_CLOSE_COVER, COVER)
    assert unit.writes[2:] == [(16, 0x0009), (16, 0x0001)]


async def test_light_the_controller_does_not_switch_stays_off(
    hass: HomeAssistant, monkeypatch
) -> None:
    from custom_components.pool_modbus.library.devices import pool_controller

    monkeypatch.setattr(pool_controller, "LIGHT_WAIT", 0.05)
    unit = fake_unit("pool_controller")  # cover closed: the controller ignores the toggle
    await add(hass, "pool_controller", unit)

    await call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT)  # no error

    assert unit.writes == [(16, 0x0011), (16, 0x0001)]
    assert state(hass, LIGHT) == "off"


async def test_light(hass: HomeAssistant) -> None:
    unit = fake_unit("pool_controller")
    unit.registers[24] |= 1 << 3  # cover open
    await add(hass, "pool_controller", unit)

    await call(hass, LIGHT_DOMAIN, SERVICE_TURN_ON, LIGHT)

    assert unit.writes == [(16, 0x0011), (16, 0x0001)]
    assert state(hass, LIGHT) == "on"
    assert hass.states.get(LIGHT).attributes["icon"] == "mdi:lightbulb-on"


async def test_backwash_schedule(hass: HomeAssistant) -> None:
    unit = fake_unit("pool_controller")
    await add(hass, "pool_controller", unit)

    await call(hass, SELECT_DOMAIN, SERVICE_SELECT_OPTION, DAY, **{ATTR_OPTION: "Monday"})
    await call(hass, TIME_DOMAIN, SERVICE_SET_VALUE, TIME, **{ATTR_TIME: "07:30:00"})

    assert unit.writes == [
        (17, 1),
        (16, 0x8001),
        (16, 0x0001),
        (18, [7, 30]),
        (16, 0x8001),
        (16, 0x0001),
    ]
    assert state(hass, TIME) == "07:30:00"
    assert state(hass, "sensor.pool_controller_saved_backwash_schedule") == "Monday 07:30"


async def test_buttons(hass: HomeAssistant, freezer) -> None:
    unit = fake_unit("pool_controller")
    await add(hass, "pool_controller", unit)

    await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, BACKWASH)
    await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, RESET)
    assert unit.writes == [(16, 0x0003), (16, 0x0001), (16, 0x4001), (16, 0x0001)]

    freezer.move_to(datetime(2026, 10, 5, 14, 30, 15, tzinfo=dt_util.get_default_time_zone()))
    await call(hass, BUTTON_DOMAIN, SERVICE_PRESS, SYNC)
    assert unit.writes[4] == (32, [15 << 8 | 1, 14 << 8 | 30, 10 << 8 | 5, 20 << 8 | 26])


async def test_optional_parts_take_their_entities(hass: HomeAssistant) -> None:
    options = {"read_backwash_schedule": False, "read_clock": False}
    await add(hass, "pool_controller", fake_unit("pool_controller"), options)
    for gone in (DAY, TIME, SYNC, "sensor.pool_controller_rtc"):
        assert hass.states.get(gone) is None, gone
    assert state(hass, FILTRATION) == "on"
