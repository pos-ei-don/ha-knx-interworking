"""Valve entities on top of the running KNX connection.

The built-in KNX integration has no ``valve`` platform (XKNX/knx-integration#1).
A motorised valve on a blind actuator channel is therefore usually set up as a
KNX cover — and then shows "open/closed" with blind icons and semantics that
invite mistakes (which way is "open" for a mixing valve?).

This platform adds real ``valve`` entities. Each one is configured as a
subentry ("Add valve") and wraps an ``xknx.devices.Cover`` registered with the
xknx instance of the built-in integration. Nothing in Home Assistant core is
patched; no telegram is sent except on an explicit command (or the normal
state read of the status address at start, like any KNX entity).

The KNX integration can be reloaded at any time, which replaces its xknx
instance. Each valve checks on a short interval whether it is still attached
to the current instance and re-registers otherwise.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity
from xknx.devices import Cover as XknxCover

from ._knx import knx_module
from .const import DOMAIN, SUBENTRY_VALVE
from .valve_logic import (
    InvalidValveConfig,
    ValveConfig,
    cover_position,
    valve_position,
)

if TYPE_CHECKING:
    from . import KnxInterworkingEntry

_LOGGER = logging.getLogger(__name__)

REATTACH_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KnxInterworkingEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one valve per subentry and keep the set in sync with the subentries."""
    known: dict[str, dict[str, Any]] = {}
    entities: dict[str, KnxInterworkingValve] = {}
    busy: set[str] = set()

    def _add(subentry: ConfigSubentry) -> None:
        known[subentry.subentry_id] = dict(subentry.data)
        try:
            config = ValveConfig.from_data(dict(subentry.data))
        except InvalidValveConfig as err:
            _LOGGER.error("Valve '%s' not created: %s", subentry.title, err)
            return
        entity = KnxInterworkingValve(hass, entry, subentry.subentry_id, config)
        entities[subentry.subentry_id] = entity
        async_add_entities([entity], config_subentry_id=subentry.subentry_id)

    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_VALVE:
            _add(subentry)

    async def _replace(subentry_id: str, sub: ConfigSubentry | None) -> None:
        """Remove the entity of a changed/removed subentry; re-add a changed one."""
        busy.add(subentry_id)
        try:
            entity = entities.pop(subentry_id, None)
            if entity is not None and entity.hass is not None and entity.platform is not None:
                await entity.async_remove(force_remove=True)
            if sub is None:
                registry = er.async_get(hass)
                unique_id = f"{entry.entry_id}_valve_{subentry_id}"
                if entity_id := registry.async_get_entity_id("valve", DOMAIN, unique_id):
                    registry.async_remove(entity_id)
            else:
                _add(sub)
        finally:
            busy.discard(subentry_id)

    @callback
    def _reconcile() -> None:
        """Subentries added, changed or removed -> add, recreate or remove entities.

        The entry's update listener sends this signal after every change, so no
        reload of the whole integration (and its features) is needed.
        """
        current = {
            s.subentry_id: s
            for s in entry.subentries.values()
            if s.subentry_type == SUBENTRY_VALVE
        }
        for subentry_id in list(known):
            if subentry_id in busy:
                continue
            sub = current.get(subentry_id)
            if sub is not None and dict(sub.data) == known[subentry_id]:
                continue
            known.pop(subentry_id)
            hass.async_create_task(_replace(subentry_id, sub))
        for subentry_id, sub in current.items():
            if subentry_id not in known and subentry_id not in busy:
                _add(sub)

    entry.async_on_unload(
        async_dispatcher_connect(hass, f"{DOMAIN}_updated_{entry.entry_id}", _reconcile)
    )


class KnxInterworkingValve(ValveEntity, RestoreEntity):
    """A KNX valve: position set + status (+ optional stop) on a blind actuator channel."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_reports_position = True

    def __init__(
        self,
        hass: HomeAssistant,
        entry: KnxInterworkingEntry,
        subentry_id: str,
        config: ValveConfig,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._config = config
        self._device: XknxCover | None = None
        self._attr_unique_id = f"{entry.entry_id}_valve_{subentry_id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{entry.entry_id}_valve_{subentry_id}")},
            "name": config.name,
            "manufacturer": "KNX",
            "model": "Valve (KNX Interworking)",
            "via_device": (DOMAIN, entry.entry_id),
        }
        features = ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE | ValveEntityFeature.SET_POSITION
        if config.supports_stop:
            features |= ValveEntityFeature.STOP
        self._attr_supported_features = features
        self._attr_device_class = (
            ValveDeviceClass(config.device_class) if config.device_class else None
        )
        self._restored_position: int | None = None

    # --- xknx device lifecycle -------------------------------------------------

    def _xknx(self) -> Any | None:
        return getattr(knx_module(self.hass), "xknx", None)

    @callback
    def _attach(self) -> None:
        """Create the xknx Cover on the current xknx instance and register it."""
        xknx = self._xknx()
        if xknx is None:
            return
        device = XknxCover(xknx, name=self.entity_id or self._config.name, **self._config.cover_kwargs())
        device.register_device_updated_cb(self._device_updated)
        xknx.devices.async_add(device)
        if self._restored_position is not None and device.current_position() is None:
            # bridge the gap until the status read answers (no telegram sent)
            device.travelcalculator.set_position(cover_position(self._restored_position))
        self._device = device

    @callback
    def _detach(self) -> None:
        device, self._device = self._device, None
        if device is None:
            return
        device.unregister_device_updated_cb(self._device_updated)
        try:
            device.xknx.devices.async_remove(device)
        except Exception:  # old instance already torn down by a KNX reload
            _LOGGER.debug("Detach of %s from a stale xknx instance failed", self.entity_id)

    @callback
    def _check_attached(self, _now: Any = None) -> None:
        """Re-register after a reload of the KNX integration replaced its xknx instance."""
        xknx = self._xknx()
        if xknx is None:
            if self._device is not None:
                self.async_write_ha_state()  # becomes unavailable
            return
        if self._device is None or self._device.xknx is not xknx:
            _LOGGER.info("%s: KNX connection replaced, re-attaching", self.entity_id)
            self._detach()
            self._attach()
            self.async_write_ha_state()

    @callback
    def _device_updated(self, _device: XknxCover) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            pos = last.attributes.get("current_position")
            if isinstance(pos, (int, float)):
                self._restored_position = int(pos)
        self._attach()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._check_attached, REATTACH_INTERVAL)
        )

    async def async_will_remove_from_hass(self) -> None:
        self._detach()

    # --- state -------------------------------------------------------------------

    @property
    def available(self) -> bool:
        module = knx_module(self.hass)
        return self._device is not None and bool(getattr(module, "connected", False))

    @property
    def current_valve_position(self) -> int | None:
        if self._device is None:
            return None
        return valve_position(self._device.current_position())

    @property
    def is_closed(self) -> bool | None:
        pos = self.current_valve_position
        return None if pos is None else pos == 0

    @property
    def is_opening(self) -> bool:
        # xknx "closing" = moving down = towards 100 = towards valve 0. Opening a
        # valve is moving the cover up (towards cover 0).
        return self._device is not None and self._device.is_opening()

    @property
    def is_closing(self) -> bool:
        return self._device is not None and self._device.is_closing()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "group_address_position": self._config.ga_position,
            "group_address_position_state": self._config.ga_position_state,
            "group_address_stop": self._config.ga_stop,
            "invert": self._config.invert,
        }

    # --- commands ----------------------------------------------------------------

    async def async_open_valve(self) -> None:
        await self.async_set_valve_position(100)

    async def async_close_valve(self) -> None:
        await self.async_set_valve_position(0)

    async def async_set_valve_position(self, position: int) -> None:
        if self._device is None:
            raise RuntimeError("KNX is not connected")
        await self._device.set_position(cover_position(position))

    async def async_stop_valve(self) -> None:
        if self._device is not None and self._config.supports_stop:
            await self._device.stop()
