"""Shared access to the built-in KNX integration's module and xknx instance.

Kept in its own module (not in ``__init__``) so both the package setup and the
features can import it without a circular import. This is the single place that
knows *how* to find the KNX integration in ``hass.data`` — everything else asks
here instead of reaching into ``hass.data`` with an ad-hoc key.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .const import KNX_DOMAIN


def knx_module(hass: HomeAssistant) -> Any | None:
    """Return the running KNXModule, or None if KNX is not loaded.

    The built-in integration stores it under ``HassKey("knx")``. Resolve the key
    by import when possible and fall back to the plain domain string, so a moved
    constant does not break us outright.
    """
    try:
        from homeassistant.components.knx.const import KNX_MODULE_KEY

        module = hass.data.get(KNX_MODULE_KEY)
        if module is not None:
            return module
    except ImportError:
        pass
    return hass.data.get(KNX_DOMAIN)


def xknx(hass: HomeAssistant) -> Any | None:
    """Return the live xknx instance, or None if KNX is not loaded."""
    return getattr(knx_module(hass), "xknx", None)
