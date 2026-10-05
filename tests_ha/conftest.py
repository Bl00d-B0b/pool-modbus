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
    """A device with standard addressing; ``odd_step`` answers like an EMEC LD controller."""

    def __init__(self, registers: dict[int, int], *, odd_step: bool = False) -> None:
        self.registers = registers
        self.step = 2 if odd_step else 1
        self.fail: Exception | None = None
        self.connected = True

    def set_message_spacing(self, seconds: float) -> None:
        pass

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        if self.fail is not None:
            raise self.fail
        return [self.registers.get(address + self.step * i, 0) for i in range(count)]


def fake_unit(device_type: str) -> FakeUnit:
    if device_type == "emec_ld":
        return FakeUnit(snapshot("ldphcl"), odd_step=True)
    return FakeUnit(snapshot(device_type))
