"""One walk over the KNX devices, shared by the group-address diagnostics.

Both the DPT-conflict check and the duplicate-writer check answer the same kind
of question — "do two parts of this installation disagree about one group
address?" — from the same data. Walking the device tree twice would be wasteful
and, worse, would let the two views drift apart.

Works for YAML **and** UI entities, because it reads the running xknx devices
rather than the config store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)


@dataclass
class GaUse:
    """How one group address is used across all KNX devices."""

    address: str
    writers: list[str] = field(default_factory=list)  # "device / feature"
    readers: list[str] = field(default_factory=list)
    dpts: dict[str, list[str]] = field(default_factory=dict)  # dpt name -> users


def _addresses(value: Any) -> list[Any]:
    """Normalise what xknx stores on a remote value into a list.

    ``unpack_group_addresses`` returns a **single** ``DeviceGroupAddress`` or
    ``None`` (extra addresses go to ``passive_group_addresses``), while the
    passive list really is a list. Treating the single one as iterable raises
    ``TypeError: 'GroupAddress' object is not iterable`` — which is exactly how
    this was found.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v is not None]
    return [value]


def _dpt_name(remote_value: Any) -> str:
    """Readable DPT of a remote value, or 'raw' when it has none."""
    cls = getattr(remote_value, "dpt_class", None)
    if cls is None:
        length = getattr(remote_value, "payload_length", None)
        return f"raw({length})" if length is not None else "raw"
    namer = getattr(cls, "dpt_name", None)
    try:
        return namer() if callable(namer) else cls.__name__
    except Exception:
        return cls.__name__


def scan_with_readable(xknx: Any) -> tuple[dict[str, GaUse], dict[str, str]]:
    """One walk over the devices → (full usage map, addresses HA reads itself).

    ``readable`` are the state addresses HA polls on its own (``_sync_state``),
    i.e. the ones that produce a GroupValueRead at startup and time out when the
    device has no read flag. A caller that needs both (the project check) gets
    them from a single pass instead of walking the device tree twice.
    """
    uses: dict[str, GaUse] = {}
    readable: dict[str, str] = {}

    def note(addr: Any, who: str, dpt: str, *, writing: bool) -> None:
        if addr is None:
            return
        key = str(addr)
        use = uses.setdefault(key, GaUse(address=key))
        (use.writers if writing else use.readers).append(who)
        use.dpts.setdefault(dpt, []).append(who)

    for device in getattr(xknx, "devices", []):
        for rv in device._iter_remote_values():
            who = f"{getattr(rv, 'device_name', '?')} / {getattr(rv, 'feature_name', '?')}"
            dpt = _dpt_name(rv)
            for addr in _addresses(getattr(rv, "group_address", None)):
                note(addr, who, dpt, writing=True)
            state = _addresses(getattr(rv, "group_address_state", None))
            for addr in state:
                note(addr, who, dpt, writing=False)
            for addr in _addresses(getattr(rv, "passive_group_addresses", None)):
                note(addr, who, dpt, writing=False)
            if getattr(rv, "_sync_state", False):
                for addr in state:
                    readable.setdefault(str(addr), who)
    return uses, readable


def scan(xknx: Any) -> dict[str, GaUse]:
    """Map every group address to the devices that write or read it."""
    return scan_with_readable(xknx)[0]


def readable_addresses(xknx: Any) -> dict[str, str]:
    """Group addresses Home Assistant reads on its own (state + sync_state)."""
    return scan_with_readable(xknx)[1]
