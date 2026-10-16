"""Pool equipment over Modbus: one config entry per device."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.modbus import async_get_unit
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from modbus_connection import ModbusError

from .const import CONF_DEVICE_TYPE, CONF_LOG_TRAFFIC, CONF_UNIT_ID, DOMAIN
from .coordinator import (
    PoolModbusConfigEntry,
    PoolModbusCoordinator,
    PoolModbusData,
    connection_config,
    device_info,
    enabled_features,
)
from .entity import entity_id
from .library import get_device_type, group_model, read_plan
from .library.reader import describe_connection
from .traffic import BusTraffic, RecordingUnit

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]

RENAMED_KEYS = {
    "clock": "rtc",
    "sync_clock": "sync_rtc",
    # EMEC pH pulse output 1 became "pH Relay 1" when output 2 was added (2026-10-14).
    "pulse_rate_ph": "pulse_rate_ph_1",
    "ph_mode": "ph1_mode",
    "ph_max": "ph1_max",
    "ph_min": "ph1_min",
    "ph_max_rate": "ph1_max_rate",
    "ph_min_rate": "ph1_min_rate",
    "ph_pulse_speed": "ph1_pulse_speed",
    "pulse_rate_cl": "pulse_rate_cl_1",
    "start_delay": "no_flow",
    "ph_tank_empty": "ph_tank_1_empty",  # "pH Tank Empty" until 2026-10-15
    # the switching relays' values were "Max Value" / "Min Value" until 2026-10-15
    "ph_relay_max": "ph_relay_on",
    "ph_relay_min": "ph_relay_off",
    "cl_relay_max": "cl_relay_on",
    "cl_relay_min": "cl_relay_off",
    "delay": "no_flow",  # named Delay for a few minutes on 2026-10-14, then back
    "start_delay_time": "feeding_delay",
}
"""Value and action keys that were renamed: old key to new key."""

OLD_NAMES: dict[str, tuple[str, ...]] = {
    # The pulse outputs, named "pH Relay 1/2" and "Cl Relay" until 2026-10-14 (and
    # earlier "pH ...", "pH Output 2 ...", "Cl Relay 1 ..."), now as on the controller.
    "pulse_rate_ph_1": ("pH Pulse Rate", "pH Relay 1 Pulse Rate"),
    "ph1_mode": ("pH Dosing Mode", "pH Relay 1 Dosing Mode"),
    "ph1_max": ("pH Max Value", "pH Relay 1 Max Value", "pH Pulse 1 Max Value"),
    "ph1_min": ("pH Min Value", "pH Relay 1 Min Value", "pH Pulse 1 Min Value"),
    "ph1_max_rate": ("pH Max Pulse Rate", "pH Relay 1 Max Pulse Rate", "pH Pulse 1 Max Rate"),
    "ph1_min_rate": ("pH Min Pulse Rate", "pH Relay 1 Min Pulse Rate", "pH Pulse 1 Min Rate"),
    "ph1_pulse_speed": ("pH Pulse Speed", "pH Relay 1 Pulse Speed"),
    "pulse_rate_ph_2": ("pH Output 2 Pulse Rate", "pH Relay 2 Pulse Rate"),
    "ph2_mode": ("pH Output 2 Mode", "pH Relay 2 Dosing Mode"),
    "ph2_max": ("pH Output 2 Max Value", "pH Relay 2 Max Value", "pH Pulse 2 Max Value"),
    "ph2_min": ("pH Output 2 Min Value", "pH Relay 2 Min Value", "pH Pulse 2 Min Value"),
    "ph2_max_rate": (
        "pH Output 2 Max Pulse Rate",
        "pH Relay 2 Max Pulse Rate",
        "pH Pulse 2 Max Rate",
    ),
    "ph2_min_rate": (
        "pH Output 2 Min Pulse Rate",
        "pH Relay 2 Min Pulse Rate",
        "pH Pulse 2 Min Rate",
    ),
    "ph2_pulse_speed": ("pH Output 2 Pulse Speed", "pH Relay 2 Pulse Speed"),
    "pulse_rate_cl_1": ("Cl Relay 1 Pulse Rate", "Cl Relay Pulse Rate"),
    "cl1_mode": ("Cl Relay 1 Dosing Mode", "Cl Relay Dosing Mode"),
    "cl1_max": ("Cl Relay 1 Max Value", "Cl Relay Max Value", "Cl Pulse Max Value"),
    "cl1_min": ("Cl Relay 1 Min Value", "Cl Relay Min Value", "Cl Pulse Min Value"),
    "cl1_max_rate": ("Cl Relay 1 Max Pulse Rate", "Cl Relay Max Pulse Rate", "Cl Pulse Max Rate"),
    "cl1_min_rate": ("Cl Relay 1 Min Pulse Rate", "Cl Relay Min Pulse Rate", "Cl Pulse Min Rate"),
    "cl1_pulse_speed": ("Cl Relay 1 Pulse Speed", "Cl Relay Pulse Speed"),
    # The switching relays' on/off state, "pH Relay" / "Cl Relay" until 2026-10-14.
    "relay_ph": ("pH Relay",),
    "relay_cl": ("Cl Relay",),
    "no_flow": ("Start Delay", "Delay"),
    "ph_tank_1_empty": ("pH Tank Empty",),
    "ph_relay_on": ("pH Relay Max Value",),
    "ph_relay_off": ("pH Relay Min Value",),
    "cl_relay_on": ("Cl Relay Max Value",),
    "cl_relay_off": ("Cl Relay Min Value",),
    "feeding_delay": ("Start Delay Time",),
}
"""Former entity names by (new) key: an entity still on the id made from one of
its old names moves to the id of the new name, with its history."""

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ICONS_MODULE = Path(__file__).parent / "frontend" / "pool_icons.js"
ICONS_URL = f"/{DOMAIN}/pool_icons.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the integration's icon set (``pool:``, drawn by script/make_pool_icons.py)
    to the frontend; entities use its icons like Material Design ones. Without the
    frontend (tests, a headless setup) the entities still work, with the icon names."""
    http = getattr(hass, "http", None)
    if http is None or DATA_EXTRA_MODULE_URL not in hass.data:
        return True
    await http.async_register_static_paths([StaticPathConfig(ICONS_URL, str(ICONS_MODULE), True)])
    version = (await async_get_integration(hass, DOMAIN)).version
    add_extra_js_url(hass, f"{ICONS_URL}?v={version}")
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> bool:
    """Set up one device; devices with identical connection settings share a connection.

    Each scan group (fast, medium, slow) gets a model narrowed to the registers its
    entities use, read on that group's interval.
    """
    device_type = get_device_type(entry.data[CONF_DEVICE_TYPE])
    config = connection_config(entry.data)
    unit_id = int(entry.data[CONF_UNIT_ID])
    try:
        unit = async_get_unit(hass, entry, config.params(), unit_id)
    except HomeAssistantError as err:
        raise ConfigEntryError(str(err)) from err
    traffic = _traffic(hass, describe_connection(config))
    if entry.options.get(CONF_LOG_TRAFFIC, False):
        traffic.reporters.add(entry.entry_id)
        entry.async_on_unload(lambda: traffic.reporters.discard(entry.entry_id))
    unit = RecordingUnit(unit, traffic, unit_id)

    # One full read: it identifies the firmware for the device info.
    full = device_type.model(unit)
    try:
        await full.async_update()
    except (ModbusError, OSError, TimeoutError) as err:
        raise ConfigEntryNotReady(f"{entry.title} did not answer: {err}") from err

    features = enabled_features(device_type, entry.options)
    coordinators = {}
    for group, fields in read_plan(device_type, type(full), features).items():
        model = group_model(device_type, unit, fields)
        coordinator = PoolModbusCoordinator(hass, entry, model, group)
        await coordinator.async_config_entry_first_refresh()
        coordinators[group] = coordinator

    data = PoolModbusData(
        device_type, features, coordinators, device_info(entry, device_type, full)
    )
    data.shown = data.used_keys()
    entry.runtime_data = data
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await er.async_migrate_entries(hass, entry.entry_id, _renamed_unique_id(hass, entry))
    _remove_unused_entities(hass, entry, data)
    _reload_when_used_settings_change(hass, entry, data)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


def _entity_keys(data: PoolModbusData) -> set[str]:
    """The keys this entry creates entities for now."""
    keys = {value.key for value in data.values}
    keys |= {action.key for action in data.actions}
    for main in (data.device_type.thermostat, data.device_type.cover):
        if main is not None:
            keys.add(main.key)
    return keys


@callback
def _remove_unused_entities(
    hass: HomeAssistant, entry: PoolModbusConfigEntry, data: PoolModbusData
) -> None:
    """Remove the registry entries this entry no longer creates: settings the device's
    current mode does not use, parts switched off, keys of earlier versions. They come
    back (same entity id) when created again. Entities an earlier version hid are shown."""
    registry = er.async_get(hass)
    prefix = f"{entry.unique_id or entry.entry_id}_"
    keys = _entity_keys(data)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if registered.unique_id.removeprefix(prefix) not in keys:
            registry.async_remove(registered.entity_id)
        elif registered.hidden_by is er.RegistryEntryHider.INTEGRATION:
            registry.async_update_entity(registered.entity_id, hidden_by=None)


@callback
def _reload_when_used_settings_change(
    hass: HomeAssistant, entry: PoolModbusConfigEntry, data: PoolModbusData
) -> None:
    """Reload the entry when the device's settings in use change (e.g. its dosing mode),
    so the entities follow. Seen on two reads in a row, so one odd read cannot do it;
    a change written from Home Assistant reloads at once (see ``async_write``)."""
    groups = {v.group for v in data.device_type.enabled_values(data.features) if v.available}
    pending: list[frozenset[str]] = []

    @callback
    def check() -> None:
        if not all(c.last_update_success for c in data.coordinators.values()):
            return
        now = data.used_keys()
        if now == data.shown:
            pending.clear()
        elif pending and pending[-1] == now:
            hass.config_entries.async_schedule_reload(entry.entry_id)
        else:
            pending[:] = [now]

    for group in groups:
        coordinator = data.coordinators.get(group)
        if coordinator is not None:
            entry.async_on_unload(coordinator.async_add_listener(check))


def _renamed_unique_id(hass: HomeAssistant, entry: PoolModbusConfigEntry):
    """Give entities of a renamed key their new unique id, and entities of a renamed
    name their new entity id (when they still use the old name's id), keeping history."""
    prefix = f"{entry.unique_id or entry.entry_id}_"
    device_type = get_device_type(entry.data[CONF_DEVICE_TYPE])
    names = {value.key: value.name for value in device_type.values}
    registry = er.async_get(hass)

    @callback
    def migrate(registered: er.RegistryEntry) -> dict[str, str] | None:
        key = registered.unique_id.removeprefix(prefix)
        if key == registered.unique_id:
            return None
        new_key = RENAMED_KEYS.get(key, key)
        updates: dict[str, str] = {}
        if new_key != key:
            updates["new_unique_id"] = prefix + new_key
        if new_key in names:
            domain = registered.domain
            old_ids = {entity_id(domain, entry, name) for name in OLD_NAMES.get(new_key, ())}
            if registered.entity_id in old_ids:
                wanted = entity_id(domain, entry, names[new_key])
                if wanted != registered.entity_id and registry.async_get(wanted) is None:
                    updates["new_entity_id"] = wanted
        return updates or None

    return migrate


def _traffic(hass: HomeAssistant, name: str) -> BusTraffic:
    """The traffic of one connection, shared by every device on it."""
    connections: dict[str, BusTraffic] = hass.data.setdefault(DOMAIN, {}).setdefault("traffic", {})
    if name not in connections:
        connections[name] = BusTraffic(name)
    return connections[name]


async def _async_reload(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PoolModbusConfigEntry) -> bool:
    """Unload a device; its shared connection closes with the last device on it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
