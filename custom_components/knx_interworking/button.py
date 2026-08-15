"""Buttons that run the read-only checks and leave the result where you pressed.

A service call answers into Developer tools; a button does not get a response
channel at all. So these buttons run the same code and post the result as a
**persistent notification** — readable without opening the developer tools, and
it stays until dismissed.

Both are read-only. The conversion button runs with ``dry_run`` and therefore
never writes an entity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.persistent_notification import async_create
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FeatureState

if TYPE_CHECKING:
    from . import KnxInterworkingEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnxInterworkingEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the action buttons."""
    async_add_entities([RunDiagnosticsButton(entry), ConvertPreviewButton(entry)])


class _BaseButton(ButtonEntity):
    """Shared device info and wiring."""

    _attr_has_entity_name = True

    def __init__(self, entry: KnxInterworkingEntry) -> None:
        """Initialise."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self.translation_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "KNX Interworking",
            "manufacturer": "Community",
            "entry_type": "service",
        }

    def _notify(self, title: str, message: str) -> None:
        async_create(self.hass, message, title=title, notification_id=self._attr_unique_id)


class RunDiagnosticsButton(_BaseButton):
    """Run every switched-on diagnostic and show the result."""

    _attr_translation_key = "run_diagnostics"
    translation_key = "run_diagnostics"
    _attr_icon = "mdi:stethoscope"

    async def async_press(self) -> None:
        """Collect the reports and post them."""
        manager = self._entry.runtime_data
        lines: list[str] = []
        for key, feature in manager.features.items():
            if not key.startswith("diag_"):
                continue
            if feature.state not in (FeatureState.ACTIVE, FeatureState.DEGRADED):
                lines.append(f"- **{key}** — off ({feature.state.value})")
                continue
            try:
                report = await feature.async_report()
            except Exception as err:  # noqa: BLE001
                lines.append(f"- **{key}** — failed: {err}")
                continue
            lines.append(f"- **{key}** — {_summarise(report)}")
        if not lines:
            lines = ["No diagnostic feature is switched on."]
        self._notify(
            "KNX Interworking — diagnostics",
            "\n".join(lines)
            + "\n\nFull detail: action `knx_interworking.run_check` with a "
            "`response_variable`.",
        )


class ConvertPreviewButton(_BaseButton):
    """Preview the YAML → UI conversion. Never writes."""

    _attr_translation_key = "convert_preview"
    translation_key = "convert_preview"
    _attr_icon = "mdi:file-move-outline"

    async def async_press(self) -> None:
        """Run the conversion as a dry run and post the summary."""
        result = await self.hass.services.async_call(
            DOMAIN,
            "convert_yaml",
            {"platform": "climate", "dry_run": True},
            blocking=True,
            return_response=True,
        )
        result = result or {}
        if "error" in result:
            body = f"Error: {result['error']}"
        elif not result.get("found"):
            body = result.get("note", "Nothing found to convert.")
        else:
            body = (
                f"**climate**: {result['found']} YAML entries, "
                f"{result.get('convertible', 0)} would convert, "
                f"{result.get('with_unmapped_keys', 0)} have keys that cannot be mapped.\n\n"
                f"{result.get('warning', '')}"
            )
        self._notify("KNX Interworking — YAML conversion (preview)", body)


def _summarise(report: dict[str, Any]) -> str:
    """One line per report, whatever shape it has."""
    for key in ("count", "total", "unanswerable_count"):
        if key in report:
            extra = ""
            if "unknown_count" in report:
                extra = f", {report['unknown_count']} not in the ETS project"
            if "read_addresses" in report:
                return f"{report.get('unanswerable_count', 0)} of {report['read_addresses']} cannot answer{extra}"
            return f"{report[key]} finding(s)"
    return "no findings"
