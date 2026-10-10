"""Reading a device model so that a failed or refused read changes nothing."""

from __future__ import annotations

from modbus_connection.model import Component


async def update_or_keep(model: Component) -> None:
    """Read ``model``; if the read fails or is refused, it keeps the values it had.

    A refused read (``ImplausibleReadError``) or a failure after the first of
    several blocks has already stored values; they are put back.
    """
    # The model keeps its values in these two dicts; its read plan holds them by
    # reference, so they are restored in place.
    values, bits = model._values, model._bits
    saved_values, saved_bits = dict(values), dict(bits)
    try:
        await model.async_update()
    except BaseException:
        values.clear()
        values.update(saved_values)
        bits.clear()
        bits.update(saved_bits)
        raise
