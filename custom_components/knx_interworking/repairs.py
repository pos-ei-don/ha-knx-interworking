"""Repairs: offer a restart for file patches that were written back.

A written-back file patch only takes effect after a restart of Home Assistant. Until 0.8.1 the repair issue
said so but could only be ignored. It now offers the restart directly: the configuration is checked first,
and the restart is only triggered if the check passes.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.config import async_check_ha_config_file
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult


class RestartRepairFlow(RepairsFlow):
    """Confirm, check the configuration, restart."""

    def __init__(self, feature: str) -> None:
        self._feature = feature

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}),
                                        description_placeholders={"feature": self._feature})
        errors = await async_check_ha_config_file(self.hass)
        if errors:
            return self.async_abort(reason="config_invalid", description_placeholders={"errors": str(errors)[:500]})
        # Not blocking: the restart ends this process; the issue disappears with it (it is not persisted as fixed).
        await self.hass.services.async_call("homeassistant", "restart", blocking=False)
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None) -> RepairsFlow:
    """Every fixable issue of this integration is a restart request."""
    return RestartRepairFlow(str((data or {}).get("feature", "?")))
