"""Tests for the cover position_state_send core patch (knx_cover_position_patch.py).

Run against a Home Assistant core checkout the patch was applied to - see
README.md in this directory. Copied next to tests/components/knx/test_cover.py.
"""

import logging
from unittest.mock import patch

import pytest

from homeassistant.components.cover import ATTR_CURRENT_POSITION, CoverState
from homeassistant.components.knx.const import CoverConf
from homeassistant.components.knx.schema import CoverSchema
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant, State

from . import KnxEntityGenerator
from .conftest import KNXTestKit

from tests.common import mock_restore_cache


async def test_cover_position_state_send(hass: HomeAssistant, knx: KNXTestKit) -> None:
    """Test publishing the calculated position instead of listening on the address."""
    mock_restore_cache(
        hass,
        (State("cover.test", CoverState.OPEN, {ATTR_CURRENT_POSITION: 80}),),
    )
    await knx.setup_integration(
        {
            CoverSchema.PLATFORM: {
                CONF_NAME: "test",
                CoverSchema.CONF_MOVE_LONG_ADDRESS: "1/0/0",
                CoverSchema.CONF_POSITION_STATE_ADDRESS: "1/0/2",
                CoverConf.POSITION_STATE_SEND: True,
                CoverConf.TRAVELLING_TIME_UP: 10,
                CoverConf.TRAVELLING_TIME_DOWN: 10,
            }
        }
    )
    # the restored position is published so a display can be answered before the
    # first travel - 80 % open is 20 % for KNX, 51 of 255
    await knx.assert_write("1/0/2", (0x33,))
    # Home Assistant is the sender here, so the address is never read
    await knx.assert_no_telegram()

    await hass.services.async_call(
        "cover", "close_cover", {"entity_id": "cover.test"}, blocking=True
    )
    await knx.assert_write("1/0/0", True)


async def test_cover_position_state_send_inverted(
    hass: HomeAssistant, knx: KNXTestKit
) -> None:
    """Test the published value follows invert_position.

    The travel calculator counts 0 as open. With invert_position the group address
    counts the other way round, which xknx normally handles in the RemoteValueScaling
    ranges - the publishing device does not use those.
    """
    mock_restore_cache(
        hass,
        (State("cover.test", CoverState.OPEN, {ATTR_CURRENT_POSITION: 80}),),
    )
    await knx.setup_integration(
        {
            CoverSchema.PLATFORM: {
                CONF_NAME: "test",
                CoverSchema.CONF_MOVE_LONG_ADDRESS: "1/0/0",
                CoverSchema.CONF_POSITION_STATE_ADDRESS: "1/0/2",
                CoverConf.POSITION_STATE_SEND: True,
                CoverConf.INVERT_POSITION: True,
            }
        }
    )
    # 80 % open is 20 % for the travel calculator - an inverted actuator expects 204
    await knx.assert_write("1/0/2", (0xCC,))


async def test_cover_position_state_send_is_not_a_state_address(
    hass: HomeAssistant, knx: KNXTestKit
) -> None:
    """Test a telegram on the address does not update the position.

    This is what caused a feedback loop with a manual `expose` configuration: Home
    Assistant read its own telegram back as actuator feedback.
    """
    mock_restore_cache(
        hass,
        (State("cover.test", CoverState.OPEN, {ATTR_CURRENT_POSITION: 80}),),
    )
    await knx.setup_integration(
        {
            CoverSchema.PLATFORM: {
                CONF_NAME: "test",
                CoverSchema.CONF_MOVE_LONG_ADDRESS: "1/0/0",
                CoverSchema.CONF_POSITION_STATE_ADDRESS: "1/0/2",
                CoverConf.POSITION_STATE_SEND: True,
            }
        }
    )
    await knx.assert_write("1/0/2", (0x33,))

    await knx.receive_write("1/0/2", (0xFF,))
    state = hass.states.get("cover.test")
    assert state.attributes[ATTR_CURRENT_POSITION] == 80


async def test_cover_position_state_send_requires_address(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, knx: KNXTestKit
) -> None:
    """Test position_state_send without a position state address is rejected."""
    with caplog.at_level(logging.ERROR):
        await knx.setup_integration(
            {
                CoverSchema.PLATFORM: {
                    CONF_NAME: "test",
                    CoverSchema.CONF_MOVE_LONG_ADDRESS: "1/0/0",
                    CoverConf.POSITION_STATE_SEND: True,
                }
            }
        )
    assert "Invalid config for 'knx'" in caplog.text
    assert hass.states.get("cover.test") is None


async def test_cover_ui_position_state_send(
    hass: HomeAssistant,
    knx: KNXTestKit,
    create_ui_entity: KnxEntityGenerator,
) -> None:
    """Test publishing the position for a cover created from the UI."""
    await knx.setup_integration()
    await create_ui_entity(
        platform=Platform.COVER,
        entity_data={"name": "test"},
        knx_data={
            "ga_up_down": {"write": "1/0/0"},
            "ga_position_state": {"state": "1/0/2"},
            CoverConf.POSITION_STATE_SEND: True,
            CoverConf.TRAVELLING_TIME_UP: 10,
            CoverConf.TRAVELLING_TIME_DOWN: 10,
        },
    )
    # the address is published to, so it is not read on creation
    await knx.assert_no_telegram()

    # and it is not a state address either - a telegram must not set the position
    await knx.receive_write("1/0/2", (0xFF,))
    state = hass.states.get("cover.test")
    assert state.attributes.get(ATTR_CURRENT_POSITION) is None


async def test_cover_position_state_send_while_travelling(
    hass: HomeAssistant, knx: KNXTestKit
) -> None:
    """Test the position is published when the travel calculator advances."""
    mock_restore_cache(
        hass,
        (State("cover.test", CoverState.OPEN, {ATTR_CURRENT_POSITION: 80}),),
    )
    # no send cooldown here: xknx creates no cooldown task for 0, so the publish is
    # immediate and the test does not have to wait for real time to pass
    with patch("homeassistant.components.knx.cover.POSITION_SEND_COOLDOWN", 0):
        await knx.setup_integration(
            {
                CoverSchema.PLATFORM: {
                    CONF_NAME: "test",
                    CoverSchema.CONF_MOVE_LONG_ADDRESS: "1/0/0",
                    CoverSchema.CONF_POSITION_STATE_ADDRESS: "1/0/2",
                    CoverConf.POSITION_STATE_SEND: True,
                    CoverConf.TRAVELLING_TIME_UP: 10,
                    CoverConf.TRAVELLING_TIME_DOWN: 10,
                }
            }
        )
    await knx.assert_write("1/0/2", (0x33,))

    # the travel calculator is driven by xknx in the background, so advance it here
    device = knx.xknx.devices["test"]
    device.travelcalculator.set_position(60)
    device.after_update()
    await hass.async_block_till_done()
    await knx.assert_write("1/0/2", (0x99,))

    # standing still does not publish again
    device.after_update()
    await hass.async_block_till_done()
    await knx.assert_no_telegram()
