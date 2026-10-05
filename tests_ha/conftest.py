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


@pytest.fixture(autouse=True)
def no_retry_pause(monkeypatch):
    from custom_components.pool_modbus import coordinator

    monkeypatch.setattr(coordinator, "RETRY_DELAY", 0)


def snapshot(name: str) -> dict[int, int]:
    data = json.loads((FIXTURES / f"{name}_snapshot.json").read_text(encoding="utf-8"))
    return {int(address): value for address, value in data["holding"].items()}


class FakeUnit:
    """A device with standard addressing; ``odd_step`` answers like an EMEC LD controller.

    ``stale_reads`` makes the next that many reads after a write still return the old
    value, like a gateway that answers reads from a cache. ``fail`` makes every request
    raise; ``fail_once`` only the next read, like a request that collided with another
    client's.
    """

    def __init__(self, registers: dict[int, int], *, odd_step: bool = False) -> None:
        self.registers = registers
        self.step = 2 if odd_step else 1
        self.fail: Exception | None = None
        self.fail_once: Exception | None = None
        self.connected = True
        self.stale_reads = 0
        self.writes: list[tuple[int, int]] = []
        self._stale: dict[int, list[int]] = {}  # address -> [old value, reads left]

    def set_message_spacing(self, seconds: float) -> None:
        pass

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        if self.fail is not None:
            raise self.fail
        if self.fail_once is not None:
            err, self.fail_once = self.fail_once, None
            raise err
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

    async def write_registers(self, address: int, values: list[int]) -> None:
        if self.fail is not None:
            raise self.fail
        self.writes.append((address, list(values)))
        for offset, value in enumerate(values):
            self.registers[address + offset] = value


class FakeController(FakeUnit):
    """The pool controller's reactions: the save pulse keeps the written backwash
    schedule, the light pulse toggles the light while the cover is open, and the
    clock registers 32-35 set the clock read at 36-39."""

    async def write_register(self, address: int, value: int) -> None:
        before = self.registers.get(address, 0)
        await super().write_register(address, value)
        if address != 16:
            return
        rising = value & ~before
        if rising & 1 << 15:
            for written, saved in ((17, 27), (18, 28), (19, 29)):
                self.registers[saved] = self.registers.get(written, 0)
        if rising & 1 << 4 and self.registers[24] & 1 << 3:
            self.registers[24] ^= 1 << 11

    async def write_registers(self, address: int, values: list[int]) -> None:
        await super().write_registers(address, values)
        if address == 32:
            sec_wd, hour_min, month_day, cent_year = values
            self.registers[36] = (sec_wd >> 8) << 8 | hour_min & 0xFF
            self.registers[37] = (hour_min >> 8) << 8 | sec_wd & 0xFF
            self.registers[38] = (month_day & 0xFF) << 8 | month_day >> 8
            self.registers[39] = (cent_year & 0xFF) << 8 | cent_year >> 8


def fake_unit(device_type: str) -> FakeUnit:
    if device_type == "emec_ld":
        return FakeUnit(snapshot("ldphcl"), odd_step=True)
    if device_type == "pool_controller":
        return FakeController(snapshot(device_type))
    return FakeUnit(snapshot(device_type))
