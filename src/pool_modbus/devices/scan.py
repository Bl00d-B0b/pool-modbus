"""Which model fields each scan group reads.

A device is read in up to three groups (fast, medium, slow), each on its own
interval. Each group gets its own model instance, narrowed with
``Component.restrict_fields`` to the fields its values use, so it only reads
those registers. The fields a value uses are found by running its getters on
stand-ins for the model, and can be extended with ``Value.fields``. A device
type's ``check_fields`` are added to every group, so each read can be checked.
"""

from __future__ import annotations

import contextlib
import inspect
from collections.abc import Callable, Iterable
from functools import cached_property
from typing import Any

from modbus_connection.model import Component

from .base import SCAN_GROUPS, Action, Cover, DeviceType, ScanGroup, Thermostat, Value


class _Recorder:
    """Stands in for a model: every field reads as ``fill`` and is noted."""

    def __init__(self, model: type[Component], fill: Any) -> None:
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_fill", fill)
        object.__setattr__(self, "seen", set())

    def __getattr__(self, name: str) -> Any:
        model = self._model
        if name in model.declared_fields:
            self.seen.add(name)
            return self._fill
        attribute = inspect.getattr_static(model, name)
        if isinstance(attribute, property) and attribute.fget is not None:
            return attribute.fget(self)
        if isinstance(attribute, cached_property):
            return attribute.func(self)
        raise AttributeError(name)


def _fields_read(model: type[Component], getters: Iterable[Callable[[Any], Any]]) -> set[str]:
    """Fields the getters read. They run twice, with every field 0 and then 1, so
    a check that stops early on one value still shows the fields after it."""
    found: set[str] = set()
    for getter in getters:
        for fill in (0, 1):
            recorder = _Recorder(model, fill)
            # Stand-in values need not make sense to the getter.
            with contextlib.suppress(Exception):
                getter(recorder)
            found |= recorder.seen
    return found


def value_fields(model: type[Component], value: Value) -> frozenset[str]:
    getters = [value.get] + ([value.available] if value.available else [])
    return frozenset(_fields_read(model, getters) | set(value.fields))


def thermostat_fields(model: type[Component], thermostat: Thermostat) -> frozenset[str]:
    getters = [
        thermostat.current_temperature,
        thermostat.target_temperature,
        thermostat.mode,
        thermostat.action,
    ]
    if thermostat.attributes is not None:
        getters.append(thermostat.attributes)
    return frozenset(_fields_read(model, getters))


def action_fields(model: type[Component], action: Action) -> frozenset[str]:
    getters = [action.available] if action.available else []
    return frozenset(_fields_read(model, getters) | set(action.fields))


def cover_fields(model: type[Component], cover: Cover) -> frozenset[str]:
    return frozenset(_fields_read(model, [cover.is_closed]) | set(cover.fields))


def read_plan(
    device_type: DeviceType, model: type[Component], features: Iterable[str]
) -> dict[ScanGroup, frozenset[str]]:
    """The fields each scan group reads with ``features`` switched on; groups
    without values are left out."""
    plan: dict[ScanGroup, set[str]] = {group: set() for group in SCAN_GROUPS}
    for value in device_type.enabled_values(features):
        plan[value.group] |= value_fields(model, value)
    for action in device_type.enabled_actions(features):
        plan[action.scan_group] |= action_fields(model, action)
    if device_type.thermostat is not None:
        thermostat = device_type.thermostat
        plan[thermostat.scan_group] |= thermostat_fields(model, thermostat)
    if device_type.cover is not None:
        plan[device_type.cover.scan_group] |= cover_fields(model, device_type.cover)
    return {
        group: frozenset(fields | set(device_type.check_fields))
        for group, fields in plan.items()
        if fields
    }


def group_model(device_type: DeviceType, unit: Any, fields: Iterable[str]) -> Component:
    """A model of ``device_type`` on ``unit`` that reads only ``fields``.

    ``restrict_fields`` also takes the other fields' addresses out of the readable
    ranges, which would split a group's read around them. The device types here
    declare ranges the device serves in full, so those stay, and a group still
    reads its registers in as few requests as the device allows.
    """
    model = device_type.model(unit)
    declared = type(model).register_ranges
    model.restrict_fields(fields)
    if declared is not None:
        model.register_ranges = declared
    return model
