"""Fixtures for the Home Assistant integration tests.

Run with pytest-homeassistant-custom-component installed:

    pytest tests_ha
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # makes custom_components importable

FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def snapshot(name: str) -> dict[int, int]:
    data = json.loads((FIXTURES / f"{name}_snapshot.json").read_text(encoding="utf-8"))
    return {int(address): value for address, value in data["holding"].items()}


class FakeUnit:
    """A device with standard addressing; ``odd_step`` answers like an EMEC LD controller.

    ``stale_reads`` makes the next that many reads after a write still return the old
    value, like a gateway that answers reads from a cache.
    """

    def __init__(self, registers: dict[int, int], *, odd_step: bool = False) -> None:
        self.registers = registers
        self.step = 2 if odd_step else 1
        self.fail: Exception | None = None
        self.connected = True
        self.stale_reads = 0
        self.writes: list[tuple[int, int]] = []
        self._stale: dict[int, list[int]] = {}  # address -> [old value, reads left]

    def set_message_spacing(self, seconds: float) -> None:
        pass

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        if self.fail is not None:
            raise self.fail
        addresses = [address + self.step * i for i in range(count)]
        words = [self.registers.get(a, 0) for a in addresses]
        for stale_address, entry in list(self._stale.items()):
            if stale_address in addresses:
                words[addresses.index(stale_address)] = entry[0]
                entry[1] -= 1
                if entry[1] == 0:
                    del self._stale[stale_address]
        return words

    async def write_register(self, address: int, value: int) -> None:
        if self.fail is not None:
            raise self.fail
        self.writes.append((address, value))
        if self.stale_reads:
            self._stale[address] = [self.registers.get(address, 0), self.stale_reads]
        self.registers[address] = value


def fake_unit(device_type: str) -> FakeUnit:
    if device_type == "emec_ld":
        return FakeUnit(snapshot("ldphcl"), odd_step=True)
    return FakeUnit(snapshot(device_type))
