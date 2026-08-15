"""KNX Interworking — companion integration for the built-in KNX integration.

It never replaces the KNX integration. It attaches to the running one, adds
diagnostics, and offers opt-in interworking fixes for devices that do not
behave the way the specification expects.

Design rule: if the built-in integration is missing or its internals have
changed, this integration loads and reports that clearly — it must not fail in
a way that takes anything else with it.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, KNX_DOMAIN
from .features import FeatureManager
from .features.catalog import FEATURE_CLASSES
from .services import async_register as async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

# How often we check that our hooks are still in place. Short enough that a KNX
# reload does not leave a bus feature dead for long, cheap enough to ignore:
# it is a couple of identity comparisons.
HEARTBEAT_INTERVAL = timedelta(seconds=30)

type KnxInterworkingEntry = ConfigEntry[FeatureManager]


def _knx_module(hass: HomeAssistant) -> object | None:
    """Return the running KNXModule, or None.

    The built-in integration stores it under ``HassKey("knx")``. We resolve the
    key by import when possible and fall back to the plain string, so a moved
    constant does not break us outright.
    """
    try:
        from homeassistant.components.knx.const import KNX_MODULE_KEY

        return hass.data.get(KNX_MODULE_KEY)
    except ImportError:
        return hass.data.get(KNX_DOMAIN)  # type: ignore[arg-type]


# Renames between config-entry versions. Old key -> new key.
# Renaming without migrating would silently reset a user's setting to its default
# — and for a patch feature that means "only report" instead of "restore", which
# only shows up at the next core update. Exactly the silent failure this
# integration exists to prevent.
_OPTION_RENAMES_V1_V2 = {"status_text_patch_autoapply": "patch_auto_restore"}
_FEATURE_RENAMES_V1_V2 = {"climate_status_text_patch": "patch_climate_status_text"}


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Carry stored options over to renamed keys."""
    if entry.version >= 2:
        return True

    options = dict(entry.options)
    features = dict(options.get("features", {}))
    for old, new in _FEATURE_RENAMES_V1_V2.items():
        if old in features:
            features[new] = features.pop(old)
    for old, new in _OPTION_RENAMES_V1_V2.items():
        if old in options:
            options[new] = options.pop(old)
    options["features"] = features

    hass.config_entries.async_update_entry(entry, options=options, version=2)
    _LOGGER.info(
        "Migrated configuration to version 2 (renamed %d feature key(s) and %d option(s))",
        len(_FEATURE_RENAMES_V1_V2),
        len(_OPTION_RENAMES_V1_V2),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KnxInterworkingEntry) -> bool:
    """Attach to the running KNX integration and apply the chosen features."""
    knx = _knx_module(hass)
    if knx is None:
        # Not an error: KNX may simply not be set up yet. HA will retry.
        raise ConfigEntryNotReady(
            "The KNX integration is not loaded yet. This integration attaches to it."
        )
    if not hasattr(knx, "xknx"):
        _LOGGER.error(
            "Found the KNX integration but it does not expose 'xknx'. "
            "Its internals have changed — loading without any feature"
        )

    manager = FeatureManager(hass=hass, entry=entry)
    for cls in FEATURE_CLASSES:
        manager.register(cls(hass, entry))
    await manager.async_sync()

    entry.runtime_data = manager
    async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def _heartbeat(_now: Any) -> None:
        """Reattach features that silently lost their hook.

        The KNX integration can be reloaded at any time; that replaces its xknx
        instance and takes our callback registrations with it. There is no
        reliable signal for it — SIGNAL_CONFIG_ENTRY_CHANGED covers
        added/removed/updated, not a reload — and reading the log would be a
        fragile way to learn about it. So we simply ask the features whether
        they are still attached, which also covers causes we did not foresee.
        """
        if await manager.async_verify():
            async_dispatcher_send(hass, f"{DOMAIN}_updated_{entry.entry_id}")

    entry.async_on_unload(
        async_track_time_interval(hass, _heartbeat, HEARTBEAT_INTERVAL)
    )

    active = [k for k, v in manager.summary().items() if v["state"] == "active"]
    _LOGGER.info(
        "KNX Interworking attached to KNX. %d of %d features active%s",
        len(active),
        len(manager.features),
        f": {', '.join(active)}" if active else "",
    )
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: KnxInterworkingEntry
) -> None:
    """Apply option changes without a restart."""
    await entry.runtime_data.async_sync()
    # Push the new states to the status sensor right away.
    async_dispatcher_send(hass, f"{DOMAIN}_updated_{entry.entry_id}")


async def async_unload_entry(hass: HomeAssistant, entry: KnxInterworkingEntry) -> bool:
    """Revert every feature, then unload."""
    await entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
