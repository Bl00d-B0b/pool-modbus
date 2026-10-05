"""Shared test helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from modbus_connection import GatewayTargetError

FIXTURES = Path(__file__).parent / "fixtures"


class FakeEmecPump:
    """Answer like an EMEC LD controller: byte-addressed holding registers.

    A read of N registers from an odd address returns the N values two
    addresses apart. An even start address fails the test, because the real
    controller returns byte-shifted data for it.

    ``slow_write_reply`` stores a written value but raises exception 0x0B, as
    the real controller does behind its gateway; ``lose_writes`` also drops the
    value. ``failed_reads`` makes that many reads after a write fail.
    """

    def __init__(self, registers: dict[int, int]) -> None:
        self.registers = registers
        self.requests: list[tuple[str, int, int]] = []
        self.message_spacing = 0.0
        self.connected = True
        self.slow_write_reply = False
        self.lose_writes = False
        self.failed_reads = 0
        self._failing_reads = 0

    def set_message_spacing(self, seconds: float) -> None:
        self.message_spacing = seconds

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        self.requests.append(("holding", address, count))
        assert address % 2 == 1, f"a read from even address {address} returns byte-shifted data"
        if self._failing_reads:
            self._failing_reads -= 1
            raise GatewayTargetError
        return [self.registers.get(address + 2 * i, 0) for i in range(count)]

    async def write_register(self, address: int, value: int) -> None:
        """Function 06 at a value's odd wire address."""
        self.requests.append(("write", address, value))
        assert address % 2 == 1, f"a write to even address {address} lands between two values"
        if not self.lose_writes:
            self.registers[address] = value
        self._failing_reads = self.failed_reads
        if self.slow_write_reply or self.lose_writes:
            raise GatewayTargetError

    @property
    def writes(self) -> list[tuple[int, int]]:
        return [(address, value) for kind, address, value in self.requests if kind == "write"]


class FakeUnit:
    """Answer like a device with standard addressing: N registers from a are a ... a+N-1.

    ``fail`` makes every read raise that exception, like a device that does not answer.
    ``stale_reads`` makes the next that many reads after a write still return the old
    value, like a gateway that answers reads from a cache.
    """

    def __init__(
        self, registers: dict[int, int], fail: Exception | None = None, stale_reads: int = 0
    ) -> None:
        self.registers = registers
        self.fail = fail
        self.stale_reads = stale_reads
        self.requests: list[tuple[str, int, int]] = []
        self.message_spacing = 0.0
        self.connected = True
        self._stale: dict[int, list[int]] = {}  # address -> [old value, reads left]

    def set_message_spacing(self, seconds: float) -> None:
        self.message_spacing = seconds

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        self.requests.append(("holding", address, count))
        if self.fail is not None:
            raise self.fail
        words = [self.registers.get(address + i, 0) for i in range(count)]
        for stale_address, entry in list(self._stale.items()):
            if address <= stale_address < address + count:
                words[stale_address - address] = entry[0]
                entry[1] -= 1
                if entry[1] == 0:
                    del self._stale[stale_address]
        return words

    async def write_register(self, address: int, value: int) -> None:
        """Function 06; the register then reads back the written value."""
        self.requests.append(("write", address, value))
        if self.fail is not None:
            raise self.fail
        if self.stale_reads:
            self._stale[address] = [self.registers.get(address, 0), self.stale_reads]
        self.registers[address] = value

    async def write_registers(self, address: int, values: list[int]) -> None:
        """Function 16."""
        self.requests.append(("write_many", address, list(values)))
        if self.fail is not None:
            raise self.fail
        for offset, value in enumerate(values):
            self.registers[address + offset] = value

    @property
    def writes(self) -> list[tuple[int, Any]]:
        """Every write in order: (address, value) for function 06, (address, [values]) for 16."""
        return [(address, value) for kind, address, value in self.requests if kind != "holding"]


def load_snapshot(name: str) -> dict[int, int]:
    data = json.loads((FIXTURES / f"{name}_snapshot.json").read_text(encoding="utf-8"))
    return {int(address): value for address, value in data["holding"].items()}


@pytest.fixture
def ldphcl_snapshot() -> dict[int, int]:
    """Registers read from a real LDPHCL on 2026-10-01, keyed by wire address."""
    return load_snapshot("ldphcl")


@pytest.fixture
def t010_snapshot() -> dict[int, int]:
    """Registers 0-9 read from a real T010 on 2026-10-05."""
    return load_snapshot("t010")


@pytest.fixture
def controller_snapshot() -> dict[int, int]:
    """Registers 16-39 read from the real pool controller on 2026-10-05."""
    return load_snapshot("pool_controller")


@pytest.fixture
def make_pump() -> Callable[[dict[int, int]], FakeEmecPump]:
    return FakeEmecPump


@pytest.fixture
def make_unit() -> Callable[..., FakeUnit]:
    return FakeUnit
