"""The requests on each connection, kept so a bus failure can be written to the log.

Every request a device makes goes through a ``RecordingUnit``, which notes it (time,
Modbus ID, function, address, the values read or written, or the error) in the
connection's ``BusTraffic``. All devices on one connection share it, so their requests
stay in the order they went out. At debug level every request is logged; with the
"log the bus traffic around failures" option, a run of failed requests writes the
requests before it as one warning, and the first answer after it, with the requests
that follow, as another. Frames are shown in RTU form, built from the request and the
values of its answer (a TCP gateway turns them into exactly these bytes on the bus).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

KEEP = 300
"""Requests kept per connection: a few minutes of polling."""

FAILURE_RUN = 5
"""Failed requests in a row, on any device of the connection, that count as the bus
failing; single failures (a late answer, a write the gateway gave up on) do not."""

AFTER = 10
"""Requests written after the first answer that ends a failure."""


def crc16(frame: bytes) -> bytes:
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes((crc & 0xFF, crc >> 8))


def _rtu(*parts: int | bytes) -> str:
    body = b"".join(bytes((p,)) if isinstance(p, int) else p for p in parts)
    return (body + crc16(body)).hex(" ").upper()


def _word(value: int) -> bytes:
    return (value & 0xFFFF).to_bytes(2, "big")


def _bits(values: tuple[int, ...]) -> bytes:
    packed = bytearray((len(values) + 7) // 8)
    for i, on in enumerate(values):
        if on:
            packed[i // 8] |= 1 << i % 8
    return bytes(packed)


@dataclass(frozen=True)
class Request:
    """One request and what came back."""

    time: float
    unit_id: int
    function: int
    address: int
    count: int
    values: tuple[int, ...] | None
    """The values read, or the values written."""
    error: str | None
    seconds: float

    @property
    def ok(self) -> bool:
        return self.error is None

    def request_frame(self) -> str:
        u, f, a = self.unit_id, self.function, _word(self.address)
        values = self.values or ()
        if f == 5:
            return _rtu(u, f, a, _word(0xFF00 if values and values[0] else 0))
        if f == 6:
            return _rtu(u, f, a, _word(values[0] if values else 0))
        if f == 16:
            data = b"".join(_word(v) for v in values)
            return _rtu(u, f, a, _word(len(values)), len(data), data)
        return _rtu(u, f, a, _word(self.count))

    def reply_frame(self) -> str:
        if self.error is not None:
            return self.error
        u, f = self.unit_id, self.function
        values = self.values or ()
        if f in (1, 2):
            data = _bits(values)
            return _rtu(u, f, len(data), data)
        if f in (3, 4):
            data = b"".join(_word(v) for v in values)
            return _rtu(u, f, len(data), data)
        if f == 16:
            return _rtu(u, f, _word(self.address), _word(len(values)))
        return self.request_frame()  # 05 and 06 echo the request

    @property
    def stamp(self) -> str:
        moment = dt_util.as_local(dt_util.utc_from_timestamp(self.time))
        return moment.strftime("%Y-%m-%d %H:%M:%S.") + f"{moment.microsecond // 1000:03d}"

    def __str__(self) -> str:
        return (
            f"{self.stamp} id {self.unit_id} fn {self.function:02d} {self.address}+{self.count}: "
            f"{self.request_frame()} -> {self.reply_frame()} ({self.seconds * 1000:.0f} ms)"
        )


class BusTraffic:
    """The last requests on one connection, and the failure runs among them."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.requests: deque[Request] = deque(maxlen=KEEP)
        self.reporters: set[str] = set()
        """Entries with the option on; with none, failures are only counted."""
        self._failed = 0
        self._failing_since: float | None = None
        self._after: list[Request] | None = None
        self._after_left = 0
        self._after_header = ""

    def record(self, request: Request) -> None:
        self.requests.append(request)
        _LOGGER.debug("%s: %s", self.name, request)
        if self._after is not None:
            self._after.append(request)
            self._after_left -= 1
            if self._after_left <= 0:
                self._report_after()
        if request.ok:
            if self._failing_since is not None:
                self._recovered(request)
            self._failed = 0
            return
        self._failed += 1
        if self._failed == FAILURE_RUN and self._failing_since is None:
            failures = list(self.requests)[-FAILURE_RUN:]
            self._failing_since = failures[0].time
            if self.reporters:
                _LOGGER.warning(
                    "%s: %d requests in a row failed, from %s. The last %d requests:\n%s",
                    self.name,
                    FAILURE_RUN,
                    failures[0].stamp,
                    len(self.requests),
                    "\n".join(str(r) for r in self.requests),
                )

    def _recovered(self, request: Request) -> None:
        since, self._failing_since = self._failing_since, None
        if not self.reporters or since is None:
            return
        failed = [r for r in self.requests if not r.ok and r.time >= since]
        self._after = [*failed[-FAILURE_RUN:], request]
        self._after_left = AFTER
        self._after_header = (
            f"{self.name}: answered again after {request.time - since:.1f} s "
            f"({self._failed} requests failed in a row)"
        )

    def _report_after(self) -> None:
        after, self._after = self._after or [], None
        _LOGGER.warning(
            "%s. The last failures, the first answer and the requests after it:\n%s",
            self._after_header,
            "\n".join(str(r) for r in after),
        )


class RecordingUnit:
    """A Modbus unit that notes each of its requests in its connection's traffic."""

    def __init__(
        self,
        unit: Any,
        traffic: BusTraffic,
        unit_id: int,
        private: tuple[int, ...] = (),
        step: int = 1,
    ) -> None:
        self._unit = unit
        self._traffic = traffic
        self._unit_id = unit_id
        self._private = frozenset(private)
        self._step = step

    def _masked(self, address: int, values: tuple[int, ...] | None) -> tuple[int, ...] | None:
        """The values with those at private addresses (a PIN) replaced by 0."""
        if values is None or not self._private:
            return values
        return tuple(
            0 if address + self._step * i in self._private else v for i, v in enumerate(values)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._unit, name)

    async def _call(
        self,
        function: int,
        address: int,
        count: int,
        written: tuple[int, ...] | None,
        call: Awaitable[Any],
    ) -> Any:
        start, began = time.time(), time.monotonic()
        try:
            result = await call
        except Exception as err:
            self._traffic.record(
                Request(
                    start,
                    self._unit_id,
                    function,
                    address,
                    count,
                    self._masked(address, written),
                    f"{type(err).__name__}: {err}" if str(err) else type(err).__name__,
                    time.monotonic() - began,
                )
            )
            raise
        values = written if result is None else tuple(int(v) for v in result)
        values = self._masked(address, values)
        self._traffic.record(
            Request(
                start,
                self._unit_id,
                function,
                address,
                count,
                values,
                None,
                time.monotonic() - began,
            )
        )
        return result

    async def read_coils(self, address: int, count: int) -> list[bool]:
        return await self._call(1, address, count, None, self._unit.read_coils(address, count))

    async def read_discrete_inputs(self, address: int, count: int) -> list[bool]:
        return await self._call(
            2, address, count, None, self._unit.read_discrete_inputs(address, count)
        )

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        return await self._call(
            3, address, count, None, self._unit.read_holding_registers(address, count)
        )

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        return await self._call(
            4, address, count, None, self._unit.read_input_registers(address, count)
        )

    async def write_coil(self, address: int, value: bool) -> None:
        await self._call(5, address, 1, (int(bool(value)),), self._unit.write_coil(address, value))

    async def write_register(self, address: int, value: int) -> None:
        await self._call(6, address, 1, (value,), self._unit.write_register(address, value))

    async def write_registers(self, address: int, values: list[int]) -> None:
        await self._call(
            16, address, len(values), tuple(values), self._unit.write_registers(address, values)
        )
