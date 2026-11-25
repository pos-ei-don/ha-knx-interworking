"""Unit tests for the valve platform's pure logic — no Home Assistant needed.

    python -m pytest tests/test_valve_logic.py -p no:homeassistant

The bus-level tests drive a real ``xknx.devices.Cover`` on an unconnected
``XKNX`` instance and read the outgoing telegram from its queue, so the meaning
of ``invert`` is pinned to the bytes that reach the bus.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "knx_interworking")
)

import valve_logic as vl  # noqa: E402
from xknx import XKNX  # noqa: E402
from xknx.devices import Cover  # noqa: E402
from xknx.dpt import DPTArray  # noqa: E402
from xknx.telegram import GroupAddress, Telegram  # noqa: E402
from xknx.telegram.apci import GroupValueWrite  # noqa: E402

MISCHER = {
    vl.CONF_NAME: "HK2 Mischer",
    vl.CONF_GA_POSITION: "1/4/168",
    vl.CONF_GA_POSITION_STATE: "1/4/169",
    vl.CONF_GA_STOP: "",
    vl.CONF_TRAVEL_TIME_DOWN: 123,
    vl.CONF_TRAVEL_TIME_UP: 123,
    vl.CONF_INVERT: True,
    vl.CONF_DEVICE_CLASS: "none",
}


def test_from_data_normalises_and_round_trips():
    cfg = vl.ValveConfig.from_data(MISCHER)
    assert cfg.ga_position == "1/4/168"
    assert cfg.ga_position_state == "1/4/169"
    assert cfg.ga_stop is None and not cfg.supports_stop
    assert cfg.travel_time_down == 123.0 and cfg.invert is True
    assert cfg.device_class is None
    assert vl.ValveConfig.from_data(cfg.as_data()) == cfg


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (vl.CONF_NAME, "  "),
        (vl.CONF_GA_POSITION, ""),
        (vl.CONF_GA_POSITION, "1/4/9999"),
        (vl.CONF_GA_POSITION, "0/0/0"),
        (vl.CONF_GA_POSITION_STATE, "abc"),
        (vl.CONF_GA_STOP, "32/0/1"),
        (vl.CONF_TRAVEL_TIME_DOWN, 0),
        (vl.CONF_TRAVEL_TIME_UP, 3601),
        (vl.CONF_TRAVEL_TIME_UP, "x"),
        (vl.CONF_DEVICE_CLASS, "steam"),
    ],
)
def test_from_data_rejects_with_field(field, value):
    with pytest.raises(vl.InvalidValveConfig) as exc:
        vl.ValveConfig.from_data({**MISCHER, field: value})
    assert exc.value.field == field


def test_optional_addresses_and_stop():
    cfg = vl.ValveConfig.from_data({**MISCHER, vl.CONF_GA_POSITION_STATE: "", vl.CONF_GA_STOP: "1/4/170"})
    assert cfg.ga_position_state is None
    assert cfg.supports_stop
    kw = cfg.cover_kwargs()
    assert kw["group_address_stop"] == "1/4/170" and kw["group_address_position_state"] is None
    assert kw["invert_position"] is True and kw["travel_time_down"] == 123.0


def test_position_mapping():
    assert vl.cover_position(100) == 0  # valve open = cover up
    assert vl.cover_position(0) == 100
    assert vl.cover_position(130) == 0 and vl.cover_position(-5) == 100
    assert vl.valve_position(0) == 100 and vl.valve_position(100) == 0
    assert vl.valve_position(None) is None
    for p in range(0, 101, 7):
        assert vl.valve_position(vl.cover_position(p)) == p


def _sent_position(invert: bool, valve_pos: int) -> DPTArray:
    async def run():
        xknx = XKNX()
        cfg = vl.ValveConfig.from_data({**MISCHER, vl.CONF_INVERT: invert})
        dev = Cover(xknx, name="t", **cfg.cover_kwargs())
        await dev.set_position(vl.cover_position(valve_pos))
        tel: Telegram = xknx.telegrams.get_nowait()
        assert tel.destination_address == GroupAddress("1/4/168")
        dev.async_remove_tasks()
        return tel.payload.value
    return asyncio.run(run())


def test_bus_value_with_invert_equals_valve_position():
    """invert=True (like the existing cover.hk2_mischer2): valve 100 % -> bus 100 % (255)."""
    assert _sent_position(True, 100) == DPTArray((0xFF,))
    assert _sent_position(True, 0) == DPTArray((0x00,))


def test_bus_value_without_invert_is_knx_blind_convention():
    """invert=False: KNX blind convention, 0 % = open -> valve 100 % sends 0."""
    assert _sent_position(False, 100) == DPTArray((0x00,))
    assert _sent_position(False, 0) == DPTArray((0xFF,))


def test_status_telegram_maps_to_valve_position():
    async def run(invert: bool, raw: int):
        xknx = XKNX()
        cfg = vl.ValveConfig.from_data({**MISCHER, vl.CONF_INVERT: invert})
        dev = Cover(xknx, name="t", **cfg.cover_kwargs())
        dev.process(
            Telegram(
                destination_address=GroupAddress("1/4/169"),
                payload=GroupValueWrite(DPTArray((raw,))),
            )
        )
        pos = vl.valve_position(dev.current_position())
        dev.async_remove_tasks()
        return pos

    assert asyncio.run(run(True, 0xFF)) == 100
    assert asyncio.run(run(True, 0x00)) == 0
    assert asyncio.run(run(False, 0xFF)) == 0
