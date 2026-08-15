"""Publish the summer/winter bit alongside Home Assistant's KNX time server.

Why this is an interworking feature
-----------------------------------
Many KNX devices expect the daylight-saving information as a separate 1-bit
"season" object. Home Assistant's KNX time server publishes only three
exposures — time (10.001), date (11.001) and datetime (19.001) — so the only way
to get that bit onto the bus today is **additional equipment**, typically a logic
module. The KNX Interworking model (Volume 3 Part 7) defines interworking as
devices understanding each other *without additional equipment*; removing the
need for that extra device is therefore exactly interworking, not a mere add-on.

Why it hooks onto the outgoing telegram instead of using its own timer
---------------------------------------------------------------------
DPT 19.001 already carries a ``dst`` flag inside the payload. If the season bit
were sent on an independent schedule, the two would disagree for the length of
the offset between the two schedules — measured in one real installation: the
time server sends hourly at :04, the logic module at :47, so up to 43 minutes of
contradiction across a changeover night.

Registering on the *outgoing* datetime telegram removes that by construction:

    xknx.telegram_queue.register_telegram_received_cb(
        cb, group_addresses=[GroupAddress(source)], match_for_outgoing=True)

This is public xknx API — no runtime patch. Two consequences worth knowing:

* The cadence is inherited from the time server. Switch the time server off and
  this bit goes quiet too; there is no orphaned sender left behind.
* The callback runs *after* ``send_telegram`` (``xknx/core/telegram_queue.py``),
  so the datetime telegram is already on the wire when the bit follows it. The
  order on the bus is datetime first, season bit second.

⚠️ Callbacks are invoked **synchronously** by xknx (``callback.callback(telegram)``
with no await and no task). An ``async def`` callback would silently never run,
so the callback below is sync and dispatches the send itself.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector
from homeassistant.util import dt as dt_util

from ..const import DOMAIN, ISSUE_SEASON_CONFLICT, KNX_DOMAIN, Category, Risk
from . import Feature, Precondition

_LOGGER = logging.getLogger(__name__)

CONF_SEASON_GA = "season_ga"
CONF_SEASON_SOURCE_GA = "season_source_ga"
CONF_SEASON_INVERT = "season_invert"

# DPT 1.001 (switch). The polarity is configurable because installations differ:
# the reference installation uses "1 = summer", others invert it.
SEASON_DPT = "1.001"


class SeasonBitSender(Feature):
    """Send a 1-bit season object together with every datetime telegram."""

    key = "season_bit"
    category = Category.INTERWORKING
    risk = Risk.BUS_WRITE
    default_enabled = False

    _cb_source: Any = None
    _cb_conflict: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise per-instance counters."""
        super().__init__(*args, **kwargs)
        self._sent_count = 0
        self._last_sent: str | None = None
        self._foreign_senders: dict[str, int] = {}
        self._resolved_source: str | None = None

    # --- configuration ----------------------------------------------------
    @classmethod
    def options_schema(cls, options: Mapping[str, Any]) -> dict[Any, Any]:
        """Target address, optional source address, polarity."""
        return {
            vol.Optional(
                CONF_SEASON_GA, default=options.get(CONF_SEASON_GA, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SEASON_SOURCE_GA, default=options.get(CONF_SEASON_SOURCE_GA, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_SEASON_INVERT, default=options.get(CONF_SEASON_INVERT, False)
            ): selector.BooleanSelector(),
        }

    @property
    def target_ga(self) -> str:
        """Group address the season bit is written to."""
        return str(self.entry.options.get(CONF_SEASON_GA) or "").strip()

    @property
    def invert(self) -> bool:
        """True when the bus expects 0 = summer."""
        return bool(self.entry.options.get(CONF_SEASON_INVERT, False))

    def _configured_source(self) -> str:
        return str(self.entry.options.get(CONF_SEASON_SOURCE_GA) or "").strip()

    def _time_server_datetime_ga(self) -> str | None:
        """Read the datetime address from the KNX integration's time server.

        This reaches into the built-in integration's config store, which is not
        a public API — hence every step is guarded and a failure only means the
        user has to name the address explicitly.
        """
        knx = self.hass.data.get(KNX_DOMAIN)
        try:
            from homeassistant.components.knx.const import KNX_MODULE_KEY

            knx = self.hass.data.get(KNX_MODULE_KEY) or knx
        except ImportError:
            pass
        store = getattr(knx, "config_store", None)
        getter = getattr(store, "get_time_server_config", None)
        if not callable(getter):
            return None
        try:
            config = getter() or {}
            return (config.get("datetime") or {}).get("write") or None
        except Exception:
            _LOGGER.debug("Could not read the KNX time server configuration", exc_info=True)
            return None

    def _resolve_source(self) -> str | None:
        """Configured source wins; otherwise the time server's datetime address."""
        return self._configured_source() or self._time_server_datetime_ga()

    # --- preconditions ----------------------------------------------------
    def check_preconditions(self) -> Precondition:
        """A target address, a source telegram and the callback API are required."""
        if not self.target_ga:
            return Precondition(
                ok=False,
                detail=(
                    "No target group address configured. Enter the address of the "
                    "summer/winter object — the feature is switched on but has nothing "
                    "to write to."
                ),
            )
        source = self._resolve_source()
        if not source:
            return Precondition(
                ok=False,
                detail=(
                    "Could not determine which telegram to follow. Either switch on the "
                    "datetime exposure of the KNX time server, or name its group address "
                    "explicitly — the season bit is deliberately only sent together with "
                    "a datetime telegram, never on its own schedule."
                ),
            )
        if source == self.target_ga:
            return Precondition(
                ok=False,
                detail=(
                    f"Source and target address are identical ({source}). That would make "
                    "the feature answer its own telegrams."
                ),
            )
        knx = self.hass.data.get(KNX_DOMAIN)
        xknx = getattr(knx, "xknx", None)
        queue = getattr(xknx, "telegram_queue", None)
        register = getattr(queue, "register_telegram_received_cb", None)
        if not callable(register):
            return Precondition(
                ok=False,
                detail="xknx telegram queue does not offer register_telegram_received_cb.",
            )
        import inspect

        if "match_for_outgoing" not in inspect.signature(register).parameters:
            return Precondition(
                ok=False,
                detail=(
                    "This xknx version cannot report outgoing telegrams "
                    "(register_telegram_received_cb has no 'match_for_outgoing'). "
                    "Sending the bit on an own schedule would risk contradicting the "
                    "clock, so the feature stays off."
                ),
            )
        self._resolved_source = source
        return Precondition(ok=True)

    # --- apply / revert ---------------------------------------------------
    async def async_apply(self) -> str:
        """Follow the datetime telegram and watch the target address."""
        from xknx.telegram.address import GroupAddress

        if self._cb_source is not None:
            return self._detail()

        knx = self.hass.data[KNX_DOMAIN]
        queue = knx.xknx.telegram_queue
        source = self._resolved_source or self._resolve_source()
        if source is None:  # pragma: no cover - guarded by preconditions
            raise RuntimeError("no datetime source address")

        self._cb_source = queue.register_telegram_received_cb(
            self._on_datetime_sent,
            group_addresses=[GroupAddress(source)],
            match_for_outgoing=True,
        )
        # Conflict watch: anything arriving *incoming* on the target address was
        # sent by somebody else - our own writes are OUTGOING.
        self._cb_conflict = queue.register_telegram_received_cb(
            self._on_foreign_write,
            group_addresses=[GroupAddress(self.target_ga)],
        )
        self._resolved_source = source
        return self._detail()

    async def async_revert(self) -> None:
        """Unregister both callbacks and clear the conflict issue."""
        knx = self.hass.data.get(KNX_DOMAIN)
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        for cb in (self._cb_source, self._cb_conflict):
            if cb is None or queue is None:
                continue
            try:
                queue.unregister_telegram_received_cb(cb)
            except (ValueError, AttributeError):
                _LOGGER.debug("Callback was already gone while reverting %s", self.key)
        self._cb_source = None
        self._cb_conflict = None
        ir.async_delete_issue(self.hass, DOMAIN, self._conflict_issue_id())

    def is_attached(self) -> bool:
        """True while our callbacks sit in the callback list of the live queue.

        Comparing against the *current* queue is what makes this detect a KNX
        reload: a new xknx instance brings a fresh, empty callback list, so our
        registration object cannot be in it any more.
        """
        if self._cb_source is None:
            return False
        knx = self.hass.data.get(KNX_DOMAIN)
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        registered = getattr(queue, "telegram_received_cbs", None)
        if registered is None:
            return False
        return self._cb_source in registered

    # --- the actual work --------------------------------------------------
    def _on_datetime_sent(self, telegram: Any) -> None:
        """Send the season bit right after a datetime telegram went out.

        Must stay synchronous: xknx calls this without awaiting.
        """
        summer = bool(dt_util.now().dst())
        payload = (not summer) if self.invert else summer
        self.hass.async_create_task(
            self._async_send(payload, summer), eager_start=False
        )

    async def _async_send(self, payload: bool, summer: bool) -> None:
        """Write the bit through the documented knx.send service."""
        try:
            await self.hass.services.async_call(
                KNX_DOMAIN,
                "send",
                {"address": self.target_ga, "payload": payload, "type": SEASON_DPT},
                blocking=True,
            )
        except Exception as err:
            _LOGGER.error(
                "Could not send the season bit to %s: %s", self.target_ga, err
            )
            return
        self._sent_count += 1
        self._last_sent = dt_util.now().isoformat(timespec="seconds")
        _LOGGER.debug(
            "Season bit -> %s: %s (%s%s)",
            self.target_ga,
            payload,
            "summer" if summer else "winter",
            ", inverted" if self.invert else "",
        )

    def _on_foreign_write(self, telegram: Any) -> None:
        """Notice a second *writer* on the target address and say so."""
        from xknx.telegram.apci import GroupValueWrite

        # Only an actual write competes with us. A GroupValueRead poll (or a
        # response to one) on the same address is not a conflicting sender and
        # must not raise a false "another device also writes …" repair issue.
        if not isinstance(telegram.payload, GroupValueWrite):
            return
        source = str(getattr(telegram, "source_address", "?"))
        first_time = source not in self._foreign_senders
        self._foreign_senders[source] = self._foreign_senders.get(source, 0) + 1
        if not first_time:
            return
        _LOGGER.warning(
            "Another device (%s) also writes the season address %s. Two senders on one "
            "group address will contradict each other — switch it off in the other "
            "device (ETS) or turn this feature off",
            source,
            self.target_ga,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._conflict_issue_id(),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SEASON_CONFLICT,
            translation_placeholders={
                "address": self.target_ga,
                "sender": source,
            },
        )

    def _conflict_issue_id(self) -> str:
        return f"{self.entry.entry_id}_{self.key}_conflict"

    # --- reporting --------------------------------------------------------
    def _detail(self) -> str:
        return (
            f"following {self._resolved_source} -> writing {self.target_ga}"
            f"{' (inverted)' if self.invert else ''}"
        )

    def extra_state(self) -> dict[str, Any]:
        """What was sent, and whether anyone else writes the same address."""
        return {
            "season_target": self.target_ga,
            "season_source": self._resolved_source,
            "season_inverted": self.invert,
            "season_sent": self._sent_count,
            "season_last_sent": self._last_sent,
            "season_other_senders": dict(self._foreign_senders),
        }
