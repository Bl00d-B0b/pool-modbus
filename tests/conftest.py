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


@pytest.fixture
def ldphcl_snapshot() -> dict[int, int]:
    """Registers read from a real LDPHCL on 2026-10-01, keyed by wire address."""
    data = json.loads((FIXTURES / "ldphcl_snapshot.json").read_text(encoding="utf-8"))
    return {int(address): value for address, value in data["holding"].items()}


@pytest.fixture
def make_pump() -> Callable[[dict[int, int]], FakeEmecPump]:
    return FakeEmecPump
