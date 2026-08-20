"""Space a climate mode write and the following power write in time.

The problem
-----------
Some KNX HVAC actuators react badly when Home Assistant writes the controller
mode and the on/off command back to back: the unit acknowledges the mode, then
the power-on, and immediately reports itself off again — it never had time to
start. This was reported for an Airzone Aidoo / Hitachi unit in
XKNX/knx-integration#57; the reporter's own fix was to take the on/off write
address out of the climate entity and drive it from an automation that waits a
moment after the mode write before sending it.

What this feature does
----------------------
It packages that workaround as an opt-in feature. It watches the *outgoing*
write to the mode group address and, a configurable delay later, sends the power
command itself to a separate on/off address:

    xknx.telegram_queue.register_telegram_received_cb(
        cb, group_addresses=[GroupAddress(mode_ga)], match_for_outgoing=True)

This is the same public-API hook the season-bit feature uses — no runtime patch.

Precondition (deliberately the user's job)
------------------------------------------
The on/off address must **not** also be written by the built-in climate entity,
otherwise both would write it and the race is back. Take the on/off write
address out of the climate configuration and let this feature drive it — exactly
the reporter's setup. The feature only ever writes the one address named here.

⚠️ xknx invokes callbacks **synchronously** (no await), so the callback below is
sync and only schedules the delayed send; the send itself runs as a task.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ..const import KNX_DOMAIN, Category, Risk
from .._knx import knx_module
from . import Feature, Precondition

_LOGGER = logging.getLogger(__name__)

CONF_CCD_MODE_GA = "ccd_mode_ga"
CONF_CCD_POWER_GA = "ccd_power_ga"
CONF_CCD_DELAY_MS = "ccd_delay_ms"
CONF_CCD_OFF_VALUE = "ccd_off_value"

# The on/off object is a plain switch.
POWER_DPT = "1.001"

DEFAULT_DELAY_MS = 100
MAX_DELAY_MS = 5000


class ClimateCommandDelay(Feature):
    """Drive a climate on/off address a short delay after the mode write."""

    key = "climate_command_delay"
    category = Category.INTERWORKING
    risk = Risk.BUS_WRITE
    default_enabled = False

    _cb_mode: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise per-instance state."""
        super().__init__(*args, **kwargs)
        self._cancel_pending: Any = None
        self._sent_count = 0
        self._last_action: str | None = None

    # --- configuration ----------------------------------------------------
    @classmethod
    def options_schema(cls, options: Mapping[str, Any]) -> dict[Any, Any]:
        """Mode address to follow, power address to drive, delay, off value."""
        return {
            vol.Optional(
                CONF_CCD_MODE_GA, default=options.get(CONF_CCD_MODE_GA, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_CCD_POWER_GA, default=options.get(CONF_CCD_POWER_GA, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_CCD_DELAY_MS,
                default=options.get(CONF_CCD_DELAY_MS, DEFAULT_DELAY_MS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=MAX_DELAY_MS,
                    step=10,
                    unit_of_measurement="ms",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_CCD_OFF_VALUE, default=options.get(CONF_CCD_OFF_VALUE, "")
            ): selector.TextSelector(),
        }

    @property
    def mode_ga(self) -> str:
        """Group address whose outgoing write triggers the power command."""
        return str(self.entry.options.get(CONF_CCD_MODE_GA) or "").strip()

    @property
    def power_ga(self) -> str:
        """On/off group address this feature drives (DPT 1.001)."""
        return str(self.entry.options.get(CONF_CCD_POWER_GA) or "").strip()

    @property
    def delay_seconds(self) -> float:
        """Delay between the mode write and the power write, in seconds."""
        try:
            ms = float(self.entry.options.get(CONF_CCD_DELAY_MS, DEFAULT_DELAY_MS))
        except (TypeError, ValueError):
            ms = DEFAULT_DELAY_MS
        return max(0.0, min(ms, MAX_DELAY_MS)) / 1000.0

    @property
    def off_value(self) -> int | None:
        """Raw mode payload that means 'off', or None to always power on."""
        raw = str(self.entry.options.get(CONF_CCD_OFF_VALUE) or "").strip()
        if not raw:
            return None
        try:
            return int(raw, 0)
        except ValueError:
            return None

    # --- preconditions ----------------------------------------------------
    def check_preconditions(self) -> Precondition:
        """Both addresses, and the outgoing-callback API, are required."""
        if not self.mode_ga:
            return Precondition(
                ok=False,
                detail=(
                    "No mode group address configured. Enter the controller-mode "
                    "address the climate entity writes — its outgoing write is what "
                    "triggers the delayed power command."
                ),
            )
        if not self.power_ga:
            return Precondition(
                ok=False,
                detail=(
                    "No on/off group address configured. Enter the switch address to "
                    "drive after the delay — and make sure it is NOT also written by "
                    "the climate entity, or both would write it."
                ),
            )
        if self.mode_ga == self.power_ga:
            return Precondition(
                ok=False,
                detail=(
                    f"Mode and on/off address are identical ({self.mode_ga}). That "
                    "would make the feature answer its own telegrams."
                ),
            )
        knx = knx_module(self.hass)
        xknx = getattr(knx, "xknx", None)
        queue = getattr(xknx, "telegram_queue", None)
        register = getattr(queue, "register_telegram_received_cb", None)
        if not callable(register):
            return Precondition(
                ok=False,
                detail="KNX is not loaded yet, or its telegram queue is unavailable.",
            )
        import inspect

        if "match_for_outgoing" not in inspect.signature(register).parameters:
            return Precondition(
                ok=False,
                detail=(
                    "This xknx version cannot report outgoing telegrams "
                    "(register_telegram_received_cb has no 'match_for_outgoing')."
                ),
            )
        return Precondition(ok=True)

    # --- lifecycle --------------------------------------------------------
    async def async_apply(self) -> str:
        """Register the outgoing-mode callback."""
        from xknx.telegram.address import GroupAddress

        knx = knx_module(self.hass)
        queue = knx.xknx.telegram_queue
        self._cb_mode = queue.register_telegram_received_cb(
            self._on_mode_written,
            group_addresses=[GroupAddress(self.mode_ga)],
            match_for_outgoing=True,
        )
        off = self.off_value
        off_note = f", off when mode == {off}" if off is not None else ""
        return (
            f"Following {self.mode_ga} → power {self.power_ga} after "
            f"{int(self.delay_seconds * 1000)} ms{off_note}."
        )

    async def async_revert(self) -> None:
        """Cancel any pending send and unregister the callback."""
        if self._cancel_pending is not None:
            self._cancel_pending()
            self._cancel_pending = None
        knx = knx_module(self.hass)
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        if queue is not None and self._cb_mode is not None:
            try:
                queue.unregister_telegram_received_cb(self._cb_mode)
            except Exception:  # noqa: BLE001 - revert must never raise
                _LOGGER.debug("Mode callback was already gone", exc_info=True)
        self._cb_mode = None

    def is_attached(self) -> bool:
        """True while the callback is still registered on the live queue."""
        knx = knx_module(self.hass)
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        registered = getattr(queue, "telegram_received_cbs", None)
        if registered is None or self._cb_mode is None:
            return False
        return self._cb_mode in registered

    def extra_state(self) -> dict[str, Any]:
        """Expose what the feature is doing, for the status sensor."""
        return {
            "mode_ga": self.mode_ga,
            "power_ga": self.power_ga,
            "delay_ms": int(self.delay_seconds * 1000),
            "power_writes_sent": self._sent_count,
            "last_action": self._last_action,
            "pending": self._cancel_pending is not None,
        }

    # --- the actual work --------------------------------------------------
    def _raw_value(self, telegram: Any) -> int | None:
        """Best-effort raw integer of the mode telegram's payload."""
        from xknx.dpt import DPTArray, DPTBinary

        value = getattr(getattr(telegram, "payload", None), "value", None)
        if isinstance(value, DPTBinary):
            return int(value.value)
        if isinstance(value, DPTArray):
            try:
                return int.from_bytes(bytes(value.value), "big")
            except (TypeError, ValueError):
                return None
        return None

    def _on_mode_written(self, telegram: Any) -> None:
        """Schedule the power command after the configured delay.

        Sync on purpose — xknx calls this without awaiting. A new mode write
        replaces a still-pending one (debounce), so a burst of mode changes
        produces a single power write.
        """
        raw = self._raw_value(telegram)
        off = self.off_value
        power_on = not (off is not None and raw == off)

        if self._cancel_pending is not None:
            self._cancel_pending()
            self._cancel_pending = None

        self._cancel_pending = async_call_later(
            self.hass, self.delay_seconds, self._make_fire(power_on)
        )

    def _make_fire(self, power_on: bool) -> Any:
        """Build the one-shot timer callback that dispatches the send."""

        def _fire(_now: Any) -> None:
            self._cancel_pending = None
            self.hass.async_create_task(self._async_send(power_on), eager_start=False)

        return _fire

    async def _async_send(self, power_on: bool) -> None:
        """Write the on/off command through the documented knx.send service."""
        try:
            await self.hass.services.async_call(
                KNX_DOMAIN,
                "send",
                {"address": self.power_ga, "payload": power_on, "type": POWER_DPT},
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - report, do not crash the loop
            _LOGGER.error(
                "Could not send the power command to %s: %s", self.power_ga, err
            )
            return
        self._sent_count += 1
        self._last_action = (
            f"{dt_util.now().isoformat(timespec='seconds')}: "
            f"{'on' if power_on else 'off'} → {self.power_ga}"
        )
        _LOGGER.debug("Climate command delay: %s", self._last_action)
