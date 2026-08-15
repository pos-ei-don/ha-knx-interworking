"""The diagnostics as a service — one question per call, answered in full.

Why a service and not just attributes (user's idea, 2026-08-08)
---------------------------------------------------------------
State attributes are rendered into **every** state write, so they have to stay
small: the sensor truncates its lists. And the scanning features compute at
startup, so the attributes answer "how it was at boot".

A service with a response has neither limit. It answers **one question at a
time**, returns everything, and re-runs the scan — so after changing something
in ETS or in Home Assistant the answer can be asked again without a restart.

Usage (Developer tools → Actions, or in a script):

    action: knx_interworking.run_check
    data:
      check: dpt_conflicts
    response_variable: result
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN, KNX_DOMAIN
from .converters import CONVERTERS

_LOGGER = logging.getLogger(__name__)

SERVICE_RUN_CHECK = "run_check"
SERVICE_CONVERT_YAML = "convert_yaml"
ATTR_CHECK = "check"
ATTR_PLATFORM = "platform"
ATTR_DRY_RUN = "dry_run"

# Service value -> feature key. "all" runs every diagnostics feature that is on.
CHECKS: dict[str, str] = {
    "decode_errors": "diag_decode_errors",
    "dpt_conflicts": "diag_dpt_conflicts",
    "duplicate_writers": "diag_duplicate_writers",
    "project_check": "diag_project_check",
}

SCHEMA = vol.Schema(
    {vol.Optional(ATTR_CHECK, default="all"): vol.In(["all", *CHECKS])}
)

CONVERT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_PLATFORM): vol.In(sorted(CONVERTERS)),
        # Default TRUE on purpose: a service that writes by default is a trap.
        vol.Optional(ATTR_DRY_RUN, default=True): cv.boolean,
    }
)


def async_unregister(hass: HomeAssistant) -> None:
    """Remove the services (called on unload of the last entry)."""
    for name in (SERVICE_RUN_CHECK, SERVICE_CONVERT_YAML):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)


def async_register(hass: HomeAssistant) -> None:
    """Register the service once."""
    if hass.services.has_service(DOMAIN, SERVICE_RUN_CHECK):
        return

    async def _run_check(call: ServiceCall) -> ServiceResponse:
        wanted = call.data.get(ATTR_CHECK, "all")
        keys = list(CHECKS) if wanted == "all" else [wanted]

        entries = hass.config_entries.async_entries(DOMAIN)
        managers = [e.runtime_data for e in entries if getattr(e, "runtime_data", None)]
        if not managers:
            return {"error": "KNX Interworking is not set up."}
        manager = managers[0]

        result: dict[str, Any] = {}
        skipped: dict[str, str] = {}
        for name in keys:
            feature = manager.features.get(CHECKS[name])
            if feature is None:
                skipped[name] = "unknown check"
                continue
            if feature.state.value not in ("active", "degraded"):
                # Deliberately not switching it on: a service call must not
                # change the configuration behind the user's back.
                skipped[name] = f"feature is {feature.state.value} — switch it on in the options"
                continue
            try:
                result[name] = await feature.async_report()
            except Exception as err:
                _LOGGER.exception("Check '%s' failed", name)
                skipped[name] = f"{type(err).__name__}: {err}"

        return {
            "generated_at": dt_util.now().isoformat(timespec="seconds"),
            "checks": result,
            **({"skipped": skipped} if skipped else {}),
        }

    async def _convert_yaml(call: ServiceCall) -> ServiceResponse:
        platform = call.data[ATTR_PLATFORM]
        dry_run = call.data[ATTR_DRY_RUN]
        knx = hass.data.get(KNX_DOMAIN)
        if knx is None:
            return {"error": "The KNX integration is not loaded."}

        # Re-read configuration.yaml: HA does not keep the raw YAML around, and
        # reading it again is also what a reload does.
        from homeassistant.config import async_hass_config_yaml

        try:
            raw = await async_hass_config_yaml(hass)
        except Exception as err:
            return {"error": f"could not read configuration.yaml: {err}"}
        entries = (raw.get(KNX_DOMAIN) or {}).get(platform) or []
        if not entries:
            return {"platform": platform, "found": 0,
                    "note": f"no YAML '{platform}' entries in configuration.yaml — nothing to convert"}

        from homeassistant.components.knx.storage.entity_store_schema import (
            KNX_SCHEMA_FOR_PLATFORM,
        )
        from homeassistant.components.knx.storage.entity_store_validation import (
            validate_entity_data,
        )

        schema = KNX_SCHEMA_FOR_PLATFORM.get(platform)
        ui_keys = {str(getattr(k, "schema", k)) for k in getattr(schema, "schema", {})}

        results: list[dict[str, Any]] = []
        for entry in entries:
            converted = CONVERTERS[platform](dict(entry), ui_keys)
            item: dict[str, Any] = {
                "name": (entry.get("name") or "(unnamed)"),
                "unmapped": converted["unmapped"],
                "notes": converted["notes"],
            }
            payload = {"platform": platform, "data": converted["data"]}
            try:
                validate_entity_data(payload)
                item["valid"] = True
            except Exception as err:
                item["valid"] = False
                item["validation_error"] = str(getattr(err, "validation_error", err))[:400]

            if dry_run or not item["valid"]:
                item["would_create"] = item["valid"]
            else:
                try:
                    item["entity_id"] = await knx.config_store.create_entity(
                        platform, converted["data"]
                    )
                    item["created"] = True
                except Exception as err:
                    item["created"] = False
                    item["error"] = f"{type(err).__name__}: {err}"
            results.append(item)

        return {
            "platform": platform,
            "dry_run": dry_run,
            "found": len(entries),
            "convertible": sum(1 for r in results if r.get("valid")),
            "with_unmapped_keys": sum(1 for r in results if r["unmapped"]),
            "entities": results,
            "warning": (
                "Converted entities get a NEW entity_id. Dashboards, automations and "
                "recorded history keep pointing at the old one. Comment out the YAML "
                "entry and restart before renaming."
            ),
        }

    hass.services.async_register(
        DOMAIN, SERVICE_CONVERT_YAML, _convert_yaml,
        schema=CONVERT_SCHEMA, supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_CHECK,
        _run_check,
        schema=SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
