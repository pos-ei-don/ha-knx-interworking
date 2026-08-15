"""Report telegrams that could not be decoded — grouped by group address.

Why this exists
---------------
xknx logs a decoding error and moves on. In a grown installation that line
scrolls past among thousands of others, and nobody connects it to the entity
that stays ``unknown``. This feature turns those events into a **list per group
address**, with the raw values that were actually on the bus and the sender.

That is exactly how the MDT actuator behind this whole project was found: out of
562,249 telegrams, two objects with raw values 8/12/24/28/32. It also produces
precisely the information a manufacturer report needs — device, address,
configured DPT, raw value.

⚠️ Interaction with the reserved-bit masking: that feature corrects the payload
**before** the decoder runs, so corrected telegrams no longer show up here. That
is intended — what remains here is what is still broken. The corrected ones are
counted by the masking feature itself.

Read-only: it registers a listener and never sends.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from homeassistant.util import dt as dt_util

from ..const import Category, Risk
from . import Feature, Precondition

_LOGGER = logging.getLogger(__name__)

MAX_ADDRESSES = 25  # keep the attribute payload bounded


class DecodeErrorMonitor(Feature):
    """Collect undecodable telegrams per group address."""

    key = "diag_decode_errors"
    category = Category.DIAGNOSTICS
    risk = Risk.READ_ONLY
    default_enabled = False

    _cb: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise counters."""
        super().__init__(*args, **kwargs)
        self._per_address: Counter[str] = Counter()
        self._raw: dict[str, Counter[str]] = {}
        self._dpt: dict[str, str] = {}
        self._sender: dict[str, str] = {}
        self._last: dict[str, str] = {}
        self._total = 0

    def check_preconditions(self) -> Precondition:
        """Needs the running xknx telegram queue."""
        knx = self.hass.data.get("knx")
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        if not callable(getattr(queue, "register_telegram_received_cb", None)):
            return Precondition(
                ok=False, detail="KNX is not running — no telegram queue to listen on."
            )
        return Precondition(ok=True)

    async def async_apply(self) -> str:
        """Listen to every incoming telegram."""
        if self._cb is not None:
            return self._detail()
        queue = self.hass.data["knx"].xknx.telegram_queue
        self._cb = queue.register_telegram_received_cb(self._on_telegram)
        return self._detail()

    async def async_revert(self) -> None:
        """Stop listening."""
        knx = self.hass.data.get("knx")
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        if self._cb is not None and queue is not None:
            try:
                queue.unregister_telegram_received_cb(self._cb)
            except (ValueError, AttributeError):
                _LOGGER.debug("Listener was already gone while reverting %s", self.key)
        self._cb = None

    def is_attached(self) -> bool:
        """Still registered on the current queue?"""
        if self._cb is None:
            return False
        knx = self.hass.data.get("knx")
        queue = getattr(getattr(knx, "xknx", None), "telegram_queue", None)
        registered = getattr(queue, "telegram_received_cbs", None)
        return registered is not None and self._cb in registered

    # --- the actual work --------------------------------------------------
    def _on_telegram(self, telegram: Any) -> None:  # noqa: ANN401
        """Record a telegram that has a known DPT but could not be decoded.

        Must stay synchronous — xknx calls listeners without awaiting.
        """
        if telegram.decoded_data is not None:
            return  # decoded fine
        knx = self.hass.data.get("knx")
        ga_dpt = getattr(getattr(knx, "xknx", None), "group_address_dpt", None)
        if ga_dpt is None:
            return
        transcoder = ga_dpt.get(telegram.destination_address)
        if transcoder is None:
            return  # no DPT configured for this address — not an error, just unknown
        payload = getattr(telegram.payload, "value", None)
        if payload is None:
            return  # a read request, not a value

        address = str(telegram.destination_address)
        self._total += 1
        self._per_address[address] += 1
        raw = getattr(payload, "value", payload)
        self._raw.setdefault(address, Counter())[str(raw)] += 1
        try:
            self._dpt[address] = transcoder.dpt_name()
        except Exception:  # noqa: BLE001
            self._dpt[address] = transcoder.__name__
        self._sender[address] = str(getattr(telegram, "source_address", "?"))
        self._last[address] = dt_util.now().isoformat(timespec="seconds")

    # --- reporting --------------------------------------------------------
    async def async_report(self) -> dict[str, Any]:
        """Every address seen, not just the worst ones. Nothing is recomputed —
        this feature collects live, so the current state IS the answer."""
        return {
            "total": self._total,
            "addresses": len(self._per_address),
            "findings": {
                addr: {
                    "count": count,
                    "dpt": self._dpt.get(addr),
                    "raw_values": dict(self._raw.get(addr, {})),
                    "sender": self._sender.get(addr),
                    "last_seen": self._last.get(addr),
                }
                for addr, count in self._per_address.most_common()
            },
        }

    def _detail(self) -> str:
        if not self._per_address:
            return "listening — no undecodable telegram seen yet"
        return f"{self._total} undecodable telegrams on {len(self._per_address)} addresses"

    def extra_state(self) -> dict[str, Any]:
        """The report: worst addresses first, with raw values and sender."""
        top = self._per_address.most_common(MAX_ADDRESSES)
        return {
            "decode_errors_total": self._total,
            "decode_error_addresses": len(self._per_address),
            "decode_errors": {
                addr: {
                    "count": count,
                    "dpt": self._dpt.get(addr),
                    "raw_values": dict(self._raw.get(addr, {}).most_common(5)),
                    "sender": self._sender.get(addr),
                    "last_seen": self._last.get(addr),
                }
                for addr, count in top
            },
            "truncated": max(0, len(self._per_address) - MAX_ADDRESSES),
        }
