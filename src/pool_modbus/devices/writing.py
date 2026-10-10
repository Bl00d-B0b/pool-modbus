"""Writing one field of a device model and confirming the device took it."""

from __future__ import annotations

import asyncio
from typing import Any

from modbus_connection import GatewayTargetError, ModbusError, ModbusTimeoutError
from modbus_connection.model import Component

from .reading import update_or_keep

CONFIRM_TIMEOUT = 5.0
"""Seconds to wait for the device to read back a written value."""

CONFIRM_INTERVAL = 0.25
"""Seconds between reads while waiting."""

NO_REPLY = (GatewayTargetError, ModbusTimeoutError)
"""Errors meaning a request got no reply, so the device may still have carried it out."""


async def write_bits(device: Component, word_field: str, mask: int, on: bool) -> None:
    """Set (``on``) or clear the bits of ``mask`` in the register behind ``word_field``.

    The new word is computed from the device's own last read and written whole:
    a read-modify-write that reads the register again inside the write can start
    from a stale word behind a gateway that answers reads from a cache, and set
    a just-cleared bit again. The write is confirmed by reading the device back.
    """
    await update_or_keep(device)
    word = getattr(device, word_field)
    if word is None:
        raise TimeoutError(f"{word_field} could not be read before writing it")
    new = word | mask if on else word & ~mask
    if new == word:
        return
    await device.write(word_field, new)
    want = mask if on else 0
    await wait_until(
        device,
        lambda d: (getattr(d, word_field) or 0) & mask == want,
        f"{word_field} bits {mask:#x} are still {'clear' if on else 'set'}",
    )


async def pulse_bits(device: Component, word_field: str, mask: int, seconds: float) -> None:
    """Set the bits of ``mask``, hold them for ``seconds``, then clear them; each
    write as ``write_bits``. The bits are cleared even if the wait is interrupted."""
    await write_bits(device, word_field, mask, True)
    try:
        await asyncio.sleep(seconds)
    finally:
        await write_bits(device, word_field, mask, False)


async def wait_until(device: Component, done: Any, what: str, timeout: float | None = None) -> None:
    """Read the device until ``done(device)`` is true; ``TimeoutError`` naming ``what``
    if it is not within ``timeout`` seconds (``CONFIRM_TIMEOUT`` by default)."""
    loop = asyncio.get_running_loop()
    limit = CONFIRM_TIMEOUT if timeout is None else timeout
    deadline = loop.time() + limit
    while True:
        await update_or_keep(device)
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

    A write that gets no reply is not an error by itself: the EMEC LD stores a
    value but answers too late for its gateway, which then reports exception
    0x0B. The reads that follow decide, and a read that fails meanwhile is
    tried again until the timeout.

    Raises ``ValueError`` for a value the field does not accept, and
    ``TimeoutError`` if the device does not show the value within
    ``CONFIRM_TIMEOUT`` seconds.
    """
    writable = getattr(type(device), field).writable
    if callable(writable):
        value = writable(value)
    await update_or_keep(device)
    current = getattr(device, field)
    if current == value:
        return
    no_reply: Exception | None = None
    try:
        await device.write(field, value)
    except NO_REPLY as err:
        no_reply = err

    loop = asyncio.get_running_loop()
    deadline = loop.time() + CONFIRM_TIMEOUT
    read_error: Exception | None = None
    while True:
        try:
            await update_or_keep(device)
        except (ModbusError, OSError, TimeoutError) as err:
            read_error = err
        else:
            read_error = None
            current = getattr(device, field)
            if current == value:
                return
        if loop.time() >= deadline:
            reads = f"cannot be read ({read_error})" if read_error else f"still reads {current}"
            unanswered = ", which got no reply" if no_reply is not None else ""
            raise TimeoutError(
                f"{field} {reads} {CONFIRM_TIMEOUT:g} s after writing {value}{unanswered}"
            ) from no_reply or read_error
        await asyncio.sleep(CONFIRM_INTERVAL)
