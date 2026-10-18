"""Restart button for written-back file patches (repairs.py)."""

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant, ServiceCall

from custom_components.knx_interworking import repairs


async def _flow(hass: HomeAssistant):
    flow = await repairs.async_create_fix_flow(hass, "x_patch_cover_position_send_restart", {"feature": "patch_cover_position_send"})
    flow.hass = hass
    return flow


async def test_confirm_shows_feature(hass: HomeAssistant) -> None:
    r = await (await _flow(hass)).async_step_init()
    assert r["type"] == "form" and r["step_id"] == "confirm"
    assert r["description_placeholders"] == {"feature": "patch_cover_position_send"}


async def test_restart_after_valid_check(hass: HomeAssistant, monkeypatch) -> None:
    calls: list[ServiceCall] = []
    hass.services.async_register("homeassistant", "restart", lambda c: calls.append(c))
    monkeypatch.setattr(repairs, "async_check_ha_config_file", AsyncMock(return_value=None))
    r = await (await _flow(hass)).async_step_confirm({})
    await hass.async_block_till_done()
    assert r["type"] == "create_entry" and len(calls) == 1


async def test_no_restart_if_config_invalid(hass: HomeAssistant, monkeypatch) -> None:
    calls: list[ServiceCall] = []
    hass.services.async_register("homeassistant", "restart", lambda c: calls.append(c))
    monkeypatch.setattr(repairs, "async_check_ha_config_file", AsyncMock(return_value="Invalid config for 'knx'"))
    r = await (await _flow(hass)).async_step_confirm({})
    await hass.async_block_till_done()
    assert r["type"] == "abort" and r["reason"] == "config_invalid" and calls == []


async def test_missing_data_does_not_crash(hass: HomeAssistant) -> None:
    flow = await repairs.async_create_fix_flow(hass, "x", None)
    flow.hass = hass
    assert (await flow.async_step_init())["description_placeholders"] == {"feature": "?"}
