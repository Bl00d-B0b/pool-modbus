"""The bus traffic log: frames, failure runs, and the option that writes them."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from custom_components.pool_modbus.traffic import (
    AFTER,
    FAILURE_RUN,
    BusTraffic,
    Request,
    crc16,
)

from .conftest import fake_unit
from .test_init import add

LOGGER = "custom_components.pool_modbus.traffic"


def request(ok: bool = True, at: float = 0.0) -> Request:
    return Request(
        at,
        2,
        3,
        1,
        8,
        (151, 0, 0, 0, 200, 0, 3, 0x0100) if ok else None,
        None if ok else "ModbusError: exception code 11",
        0.03,
    )


def test_crc16_matches_modbus() -> None:
    assert crc16(bytes((2, 3, 0, 1, 0, 8))) == bytes((0x15, 0xFF))


def test_frames_of_a_t010_read() -> None:
    read = request()
    assert read.request_frame() == "02 03 00 01 00 08 15 FF"
    assert read.reply_frame() == ("02 03 10 00 97 00 00 00 00 00 00 00 C8 00 00 00 03 01 00 CF 2F")


def test_frames_of_coils_and_writes() -> None:
    coils = Request(0, 3, 1, 0, 8, (1, 0, 0, 0, 0, 1, 0, 0), None, 0.03)
    assert coils.reply_frame().startswith("03 01 01 21 ")
    coil = Request(0, 3, 5, 58, 1, (1,), None, 0.03)
    assert coil.request_frame().startswith("03 05 00 3A FF 00 ")
    assert coil.reply_frame() == coil.request_frame()
    block = Request(0, 1, 16, 32, 2, (0x1234, 5), None, 0.03)
    assert block.request_frame().startswith("01 10 00 20 00 02 04 12 34 00 05 ")
    assert block.reply_frame().startswith("01 10 00 20 00 02 ")
    failed = request(ok=False)
    assert failed.reply_frame() == "ModbusError: exception code 11"


def test_a_failure_run_and_the_recovery_are_written(caplog) -> None:
    traffic = BusTraffic("tcp 192.168.1.50:502")
    traffic.reporters.add("entry")
    caplog.set_level(logging.WARNING, logger=LOGGER)
    for second in range(3):
        traffic.record(request(at=second))
    for second in range(3, 3 + FAILURE_RUN - 1):
        traffic.record(request(ok=False, at=second))
    assert not caplog.records  # failures, but not yet a run
    traffic.record(request(ok=False, at=10))
    assert len(caplog.records) == 1
    failure = caplog.records[0].getMessage()
    assert f"{FAILURE_RUN} requests in a row failed" in failure
    assert failure.count("02 03 00 01 00 08 15 FF") == 3 + FAILURE_RUN  # before and during

    traffic.record(request(ok=False, at=11))
    traffic.record(request(at=20))
    for second in range(21, 21 + AFTER - 1):
        traffic.record(request(at=second))
    assert len(caplog.records) == 1  # the report waits for the requests after the answer
    traffic.record(request(at=40))
    assert len(caplog.records) == 2
    recovery = caplog.records[1].getMessage()
    assert "answered again after 17.0 s (6 requests failed in a row)" in recovery
    assert recovery.count("\n") == FAILURE_RUN + 1 + AFTER


def test_without_the_option_failures_are_only_logged_at_debug(caplog) -> None:
    traffic = BusTraffic("tcp 192.168.1.50:502")
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    for second in range(FAILURE_RUN + 2):
        traffic.record(request(ok=False, at=second))
    traffic.record(request(at=20))
    assert all(r.levelno == logging.DEBUG for r in caplog.records)
    assert len(caplog.records) == FAILURE_RUN + 3  # every request, at debug


async def test_the_option_reports_a_device_that_stops_answering(
    hass: HomeAssistant, caplog
) -> None:
    unit = fake_unit("t010")
    entry = await add(hass, "t010", unit, {"log_traffic": True})
    caplog.set_level(logging.WARNING, logger=LOGGER)

    unit.fail = TimeoutError("no answer")
    for _ in range(3):  # each poll and its retry fail
        await entry.runtime_data.async_refresh_all()
    await hass.async_block_till_done()

    reports = [
        r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING
    ]
    assert len(reports) == 1
    assert "tcp 192.168.1.50:502: 5 requests in a row failed" in reports[0]
    assert "-> TimeoutError: no answer" in reports[0]
    assert "id 2 fn 03" in reports[0]


async def test_the_option_is_off_by_default(hass: HomeAssistant, caplog) -> None:
    unit = fake_unit("t010")
    entry = await add(hass, "t010", unit)
    caplog.set_level(logging.WARNING, logger=LOGGER)

    unit.fail = TimeoutError("no answer")
    for _ in range(3):
        await entry.runtime_data.async_refresh_all()
    await hass.async_block_till_done()

    assert not [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]


async def test_the_pin_never_reaches_the_log() -> None:
    """The EMEC LD's PIN (wire address 541) is logged as 0, in reads and in writes."""
    from custom_components.pool_modbus.traffic import RecordingUnit

    class Unit:
        async def read_holding_registers(self, address: int, count: int) -> list[int]:
            return [1234] * count

        async def write_register(self, address: int, value: int) -> None:
            return None

    traffic = BusTraffic("tcp 192.168.1.50:502")
    unit = RecordingUnit(Unit(), traffic, 3, (541,), 2)
    assert await unit.read_holding_registers(537, 4) == [1234] * 4  # the model gets the PIN
    await unit.write_register(541, 4321)
    read, write = traffic.requests
    assert read.values == (1234, 1234, 0, 1234)  # 537, 539, 541, 543
    assert write.values == (0,) and "4321" not in str(write) and "10E1" not in str(write)
