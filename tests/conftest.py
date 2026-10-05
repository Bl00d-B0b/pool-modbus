"""Shared test helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeEmecPump:
    """Answer like an EMEC LD controller: byte-addressed holding registers.

    A read of N registers from an odd address returns the N values two
    addresses apart. An even start address fails the test, because the real
    controller returns byte-shifted data for it.
    """

    def __init__(self, registers: dict[int, int]) -> None:
        self.registers = registers
        self.requests: list[tuple[str, int, int]] = []
        self.message_spacing = 0.0
        self.connected = True

    def set_message_spacing(self, seconds: float) -> None:
        self.message_spacing = seconds

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        self.requests.append(("holding", address, count))
        assert address % 2 == 1, f"a read from even address {address} returns byte-shifted data"
        return [self.registers.get(address + 2 * i, 0) for i in range(count)]


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

    @property
    def writes(self) -> list[tuple[int, int]]:
        return [(address, value) for kind, address, value in self.requests if kind == "write"]


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
