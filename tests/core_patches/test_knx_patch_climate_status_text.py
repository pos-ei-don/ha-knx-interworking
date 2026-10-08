"""Tests for the climate status text core patch (knx_status_text_patch.py).

Run against a Home Assistant core checkout the patch was applied to - see
README.md in this directory. Copied next to tests/components/knx/test_climate.py.
"""

from homeassistant.components.climate import HVACMode
from homeassistant.components.knx.schema import ClimateSchema
from homeassistant.const import CONF_NAME, Platform
from homeassistant.core import HomeAssistant

from . import KnxEntityGenerator
from .conftest import KNXTestKit

RAW_FLOAT_20_0 = (0x07, 0xD0)
RAW_FLOAT_21_0 = (0x0C, 0x1A)
RAW_FLOAT_22_0 = (0x0C, 0x4C)


async def test_climate_status_text(hass: HomeAssistant, knx: KNXTestKit) -> None:
    """Test KNX climate diagnostic status text (DPT 16.x)."""
    await knx.setup_integration(
        {
            ClimateSchema.PLATFORM: {
                CONF_NAME: "test",
                ClimateSchema.CONF_TEMPERATURE_ADDRESS: "1/2/3",
                ClimateSchema.CONF_TARGET_TEMPERATURE_STATE_ADDRESS: "1/2/5",
                ClimateSchema.CONF_STATUS_TEXT_STATE_ADDRESS: "1/2/17",
            }
        }
    )

    # read states state updater
    await knx.assert_read("1/2/3")
    await knx.assert_read("1/2/5")

    # StateUpdater initialize state
    await knx.receive_response("1/2/5", RAW_FLOAT_22_0)
    await knx.receive_response("1/2/3", RAW_FLOAT_21_0)

    # no value received yet -> attribute absent
    await knx.assert_read("1/2/17")
    assert "status_text" not in hass.states.get("climate.test").attributes

    # 14 byte DPT 16.001 payload, zero padded
    await knx.receive_response(
        "1/2/17",
        (0x57, 0x69, 0x20, 0x48, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    knx.assert_state("climate.test", HVACMode.HEAT, status_text="Wi H")

async def test_climate_ui_status_text(
    hass: HomeAssistant,
    knx: KNXTestKit,
    create_ui_entity: KnxEntityGenerator,
) -> None:
    """Test the diagnostic status text of a UI configured climate entity."""
    await knx.setup_integration()
    await create_ui_entity(
        platform=Platform.CLIMATE,
        entity_data={"name": "test"},
        knx_data={
            "ga_temperature_current": {"state": "0/0/1"},
            "target_temperature": {
                "ga_temperature_target": {"write": "1/1/1", "state": "1/1/2"},
            },
            "ga_status_text": {"state": "1/1/3"},
            "sync_state": True,
        },
    )
    await knx.assert_read("0/0/1", response=RAW_FLOAT_20_0)
    await knx.assert_read("1/1/2", response=RAW_FLOAT_20_0)

    # no value received yet -> attribute absent
    await knx.assert_read("1/1/3")
    assert "status_text" not in hass.states.get("climate.test").attributes

    await knx.receive_response(
        "1/1/3", (0x57, 0x69, 0x20, 0x48, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    )
    knx.assert_state("climate.test", HVACMode.HEAT, status_text="Wi H")
