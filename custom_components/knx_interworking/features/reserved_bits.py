"""Mask reserved bits in small payloads — for listed group addresses only.

Background
----------
Some actuators set bits above the width of the datapoint type in the 6-bit
"small payload" of a GroupValue telegram. The KNX Application Layer requires
unused data bits to be zero, so xknx rejects such telegrams and the value is
lost. ETS masks them and shows the value.

Upstream declined a library-wide change (XKNX/xknx#1896) with the reasoning
that the payload width is, for small payloads, the only signal that
distinguishes DPT 1 / 2 / 3 — masking unconditionally would silently decode a
DPT 3 telegram on a DPT 1 address. That reasoning is sound for a library.

This feature therefore does the narrow thing: masking happens **only** for
group addresses the user listed explicitly, and every masking is logged.

Where it hooks in
-----------------
Not in ``DPTBase.validate_payload`` — that only sees the payload, never the
destination address, so per-address scoping would be impossible there.

Instead we wrap ``GroupAddressDPT.set_decoded_data(telegram)``. It is called
once per telegram in ``TelegramQueue._process_all_telegrams``
(``xknx/core/telegram_queue.py:147``) — **before** the telegram reaches any
device — and it can resolve the transcoder for the destination address. That
makes it the one place where a per-address correction reaches every consumer.

⚠️ We rewrite ``telegram.payload.value`` in place, we do not merely fill in
``telegram.decoded_data``. That is deliberate and it was measured:

    ``select`` never uses the DPT decoder. It builds a raw value and maps raw
    integer payloads to options (``homeassistant/components/knx/select.py``,
    ``_option_payloads`` / ``option_from_payload``). A telegram whose
    ``decoded_data`` is correct but whose payload is still ``0b1100`` stays
    ``unknown`` there. Verified 2026-08-07 on ``select.example_select``.

Consequence to be honest about: downstream — including the telegram monitor in
the KNX panel — sees the corrected payload, not what the device actually put on
the bus. The original value is therefore written to the log on every single
masking, and counted per address so it can be reported to the manufacturer.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
from collections import Counter
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.helpers import selector

from ..const import Category, Risk
from . import Feature, Precondition

_LOGGER = logging.getLogger(__name__)

CONF_ADDRESSES = "reserved_bit_addresses"


class ReservedBitMasking(Feature):
    """Opt-in masking of reserved bits, scoped to configured addresses."""

    key = "reserved_bit_masking"
    category = Category.INTERWORKING
    risk = Risk.RUNTIME_PATCH
    default_enabled = False

    _original: Any = None
    _wrapper: Any = None
    _source_hash: str = ""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise per-instance counters.

        They must not live on the class: a class attribute would be shared by
        every instance and, worse, ``+=`` would silently create a shadowing
        instance attribute that the sensor might not be reading.
        """
        super().__init__(*args, **kwargs)
        self._masked_count = 0
        self._masked_per_address: Counter[str] = Counter()
        self._raw_values: Counter[str] = Counter()
        self._wrapper_errors: set[str] = set()

    # --- configuration ----------------------------------------------------
    @classmethod
    def options_schema(cls, options: Mapping[str, Any]) -> dict[Any, Any]:
        """The list of group addresses this feature is allowed to touch."""
        return {
            vol.Optional(
                CONF_ADDRESSES, default=list(options.get(CONF_ADDRESSES) or [])
            ): selector.TextSelector(
                selector.TextSelectorConfig(
                    multiple=True, type=selector.TextSelectorType.TEXT
                )
            )
        }

    @property
    def addresses(self) -> list[str]:
        """Group addresses the user opted in for."""
        raw = self.entry.options.get(CONF_ADDRESSES) or []
        return [str(a).strip() for a in raw if str(a).strip()]

    # --- preconditions ----------------------------------------------------
    def check_preconditions(self) -> Precondition:
        """Verify the hook exists and still has the shape we rely on.

        We check the *structure*, not an exact source hash: a patch release
        of xknx should not block the feature, but a refactor of the decoding
        path must.
        """
        if not self.addresses:
            return Precondition(
                ok=False,
                detail=(
                    "No group addresses configured. The feature is switched on but has "
                    "nothing to act on — add the addresses of the affected actuator."
                ),
            )
        try:
            from xknx.core.group_address_dpt import GroupAddressDPT
        except ImportError as err:
            return Precondition(ok=False, detail=f"xknx decoding module not importable: {err}")

        hook = getattr(GroupAddressDPT, "set_decoded_data", None)
        if hook is None or not callable(hook):
            return Precondition(
                ok=False,
                detail="GroupAddressDPT.set_decoded_data is missing — xknx decoding path has changed.",
            )
        params = list(inspect.signature(hook).parameters)
        if params[:2] != ["self", "telegram"]:
            return Precondition(
                ok=False,
                detail=f"Unexpected signature of set_decoded_data: ({', '.join(params)}).",
            )
        if not callable(getattr(GroupAddressDPT, "get", None)):
            return Precondition(
                ok=False,
                detail="GroupAddressDPT.get is missing — cannot resolve the transcoder per address.",
            )
        if not self._source_hash:
            # Compute once: check_preconditions also runs on the 30 s heartbeat,
            # and inspect.getsource() reads the xknx source file from disk — that
            # must not happen on the event loop on every beat. The class does not
            # change at runtime; a reimport is caught by is_attached() instead.
            try:
                self._source_hash = hashlib.sha256(
                    inspect.getsource(hook).encode()
                ).hexdigest()[:12]
            except (OSError, TypeError):
                self._source_hash = "unavailable"
        return Precondition(ok=True)

    # --- apply / revert ---------------------------------------------------
    async def async_apply(self) -> str:
        """Wrap the decoder for the configured addresses."""
        from xknx.core.group_address_dpt import GroupAddressDPT
        from xknx.dpt import DPTBinary
        from xknx.telegram.apci import GroupValueResponse, GroupValueWrite

        if self._original is not None:  # already wrapped
            return self._detail()

        self._wrapper_errors.clear()  # fresh start on (re-)enable
        original = GroupAddressDPT.set_decoded_data
        feature = self

        def set_decoded_data(self_ga: Any, telegram: Any) -> None:
            # Correct the payload first, then let xknx decode it normally: that
            # way the fix reaches the DPT-based platforms *and* the ones that
            # work on raw payloads (select).
            # Guard on _original: async_revert() sets it to None to mean "off".
            # If we could not uninstall our wrapper (another wrapper chained ours,
            # so its captured "original" *is* this closure), this makes us stop
            # masking anyway — a disabled feature must never keep changing payloads.
            if feature._original is not None:
                try:
                    feature._mask_in_place(
                        self_ga, telegram, DPTBinary, (GroupValueWrite, GroupValueResponse)
                    )
                except Exception as err:
                    # Never break the telegram loop — but a *structural* failure
                    # (e.g. a future frozen payload; the pinned xknx uses non-frozen
                    # slotted dataclasses, verified) would otherwise repeat on every
                    # telegram. Surface each distinct error once with a traceback,
                    # then suppress repeats so it does not flood the log.
                    sig = type(err).__name__
                    if sig not in feature._wrapper_errors:
                        feature._wrapper_errors.add(sig)
                        _LOGGER.exception(
                            "Reserved-bit masking hit an unexpected %s on %s — telegram "
                            "left untouched; further occurrences are suppressed",
                            sig,
                            getattr(telegram, "destination_address", "?"),
                        )
            original(self_ga, telegram)

        GroupAddressDPT.set_decoded_data = set_decoded_data  # type: ignore[method-assign]
        self._original = original
        self._wrapper = set_decoded_data
        return self._detail()

    def _mask_in_place(
        self,
        self_ga: Any,
        telegram: Any,
        dpt_binary: type,
        write_apci: tuple[type, ...],
    ) -> None:
        """Clear bits above the datapoint width, if that yields a valid value."""
        if str(telegram.destination_address) not in self.addresses:
            return  # not opted in — upstream behaviour untouched
        if not isinstance(telegram.payload, write_apci):
            return
        raw = telegram.payload.value
        if not isinstance(raw, dpt_binary):
            return  # only small payloads are in scope
        transcoder = self_ga.get(telegram.destination_address)
        if transcoder is None or transcoder.payload_type is not dpt_binary:
            return
        width = getattr(transcoder, "payload_length", 0)
        if not width:
            return
        masked = raw.value & ((1 << width) - 1)
        if masked == raw.value:
            return  # nothing reserved was set - not our case

        # Only touch the telegram if the masked value really decodes. Otherwise
        # we would destroy evidence without gaining anything.
        try:
            value = transcoder.from_knx(dpt_binary(masked))
        except Exception as err:
            _LOGGER.debug(
                "Masking %s on %s would not help (%s) — telegram left untouched",
                raw.value,
                telegram.destination_address,
                err,
            )
            return

        telegram.payload.value = dpt_binary(masked)
        address = str(telegram.destination_address)
        self._masked_count += 1
        self._masked_per_address[address] += 1
        self._raw_values[f"{address} 0b{raw.value:b}"] += 1
        # Every masking is logged: this feature changes what the rest of the
        # system sees, so it must never do so silently. The raw value is the
        # part worth reporting to the manufacturer, so it stays in the message.
        _LOGGER.warning(
            "Masked reserved bits on %s: raw %s (0b%s) -> %s, decodes as %s by %s",
            address,
            raw.value,
            format(raw.value, "b"),
            masked,
            value,
            transcoder.dpt_name(),
        )

    async def async_revert(self) -> None:
        """Restore the original decoder — only if our wrapper is still installed."""
        if self._original is None:
            return
        from xknx.core.group_address_dpt import GroupAddressDPT

        if GroupAddressDPT.set_decoded_data is self._wrapper:
            GroupAddressDPT.set_decoded_data = self._original  # type: ignore[method-assign]
        else:
            # Something replaced the decoder after we wrapped it. Restoring our
            # captured original would clobber that other wrapper (and drop its
            # chain, which calls ours). Just release our references instead.
            _LOGGER.warning(
                "reserved-bit decoder was replaced by another wrapper after we "
                "installed ours — leaving it in place, not restoring our original."
            )
        self._original = None
        self._wrapper = None

    def is_attached(self) -> bool:
        """True while our wrapper is the installed decoder.

        The patch sits on the class, so it survives a KNX reload — but not
        another integration replacing the method, and not a reimport of xknx.
        """
        if self._wrapper is None:
            return False
        try:
            from xknx.core.group_address_dpt import GroupAddressDPT
        except ImportError:
            return False
        return GroupAddressDPT.set_decoded_data is self._wrapper

    # --- reporting --------------------------------------------------------
    def _detail(self) -> str:
        count = len(self.addresses)
        return f"masking active for {count} group address{'es' if count != 1 else ''}"

    def extra_state(self) -> dict[str, Any]:
        """Expose what the feature did — that is the diagnostic value."""
        return {
            "addresses": self.addresses,
            "masked_telegrams": self._masked_count,
            "masked_per_address": dict(self._masked_per_address),
            "raw_values_seen": dict(self._raw_values),
            "hook_fingerprint": self._source_hash,
        }
