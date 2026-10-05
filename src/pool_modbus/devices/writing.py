"""Writing one field of a device model and confirming the device took it."""

from __future__ import annotations

import asyncio
from typing import Any

from modbus_connection.model import Component

CONFIRM_TIMEOUT = 5.0
"""Seconds to wait for the device to read back a written value."""

CONFIRM_INTERVAL = 0.25
"""Seconds between reads while waiting."""


async def pulse(device: Component, field: str, seconds: float) -> None:
    """Set a command bit, hold it for ``seconds``, then clear it.

    Both writes read the register back and merge, so the register's other bits
    stay as they are. The bit is cleared even if the wait is interrupted.
    """
    await device.write(field, True)
    try:
        await asyncio.sleep(seconds)
    finally:
        await device.write(field, False)


async def wait_until(device: Component, done: Any, what: str, timeout: float | None = None) -> None:
    """Read the device until ``done(device)`` is true; ``TimeoutError`` naming ``what``
    if it is not within ``timeout`` seconds (``CONFIRM_TIMEOUT`` by default)."""
    loop = asyncio.get_running_loop()
    limit = CONFIRM_TIMEOUT if timeout is None else timeout
    deadline = loop.time() + limit
    while True:
        await device.async_update()
        if done(device):
            return
        if loop.time() >= deadline:
            raise TimeoutError(f"{what} {limit:g} s later")
        await asyncio.sleep(CONFIRM_INTERVAL)


async def write_if_changed(device: Component, field: str, value: Any) -> None:
    """Write ``field`` unless the device already holds ``value``, then confirm it.

    The value is checked by the field's validator before anything is sent, and
    the device is read first rather than trusting the last poll. After the
    write the device is read until it shows the new value: some Modbus gateways
    answer reads from a cache, so a write can take a moment to show. Confirming
    also means the next read-modify-write of a shared register starts from the
    current word.

    Raises ``ValueError`` for a value the field does not accept, and
    ``TimeoutError`` if the device does not show the value within
    ``CONFIRM_TIMEOUT`` seconds.
    """
    writable = getattr(type(device), field).writable
    if callable(writable):
        value = writable(value)
    await device.async_update()
    if getattr(device, field) == value:
        return
    await device.write(field, value)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + CONFIRM_TIMEOUT
    while True:
        await device.async_update()
        current = getattr(device, field)
        if current == value:
            return
        if loop.time() >= deadline:
            raise TimeoutError(
                f"{field} still reads {current} {CONFIRM_TIMEOUT:g} s after writing {value}"
            )
        await asyncio.sleep(CONFIRM_INTERVAL)
