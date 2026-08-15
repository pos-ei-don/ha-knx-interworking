"""Status sensor: one place that shows what is active and what is not."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, FeatureState

if TYPE_CHECKING:
    from . import KnxInterworkingEntry

# The counters in the attributes only have diagnostic value if they are visible
# without changing an option first. Every value is computed live, so a poll only
# triggers a state write - it costs nothing.
SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnxInterworkingEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the status sensor."""
    async_add_entities([FeatureStatusSensor(entry)])


class FeatureStatusSensor(SensorEntity):
    """Reports how many features are active, with per-feature detail."""

    _attr_has_entity_name = True
    _attr_translation_key = "feature_status"
    _attr_should_poll = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [s.value for s in FeatureState]
    _attr_entity_registry_enabled_default = True

    def __init__(self, entry: KnxInterworkingEntry) -> None:
        """Initialise."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_feature_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "KNX Interworking",
            "manufacturer": "Community",
            "entry_type": "service",
        }

    async def async_added_to_hass(self) -> None:
        """Refresh when options change."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{DOMAIN}_updated_{self._entry.entry_id}",
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Worst state wins — so a problem is never hidden by a success."""
        states = {f.state for f in self._entry.runtime_data.features.values()}
        for worst in (FeatureState.FAILED, FeatureState.BLOCKED, FeatureState.DEGRADED):
            if worst in states:
                return worst.value
        if FeatureState.ACTIVE in states:
            return FeatureState.ACTIVE.value
        return FeatureState.DISABLED.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Per-feature state plus the safe-mode flag."""
        manager = self._entry.runtime_data
        summary = manager.summary()
        return {
            "safe_mode": manager.safe_mode,
            "active": [k for k, v in summary.items() if v["state"] == "active"],
            "features": summary,
        }
