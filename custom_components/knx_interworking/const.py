"""Constants for the KNX Interworking integration."""

from __future__ import annotations

from enum import StrEnum
from typing import Final

DOMAIN: Final = "knx_interworking"

# The built-in KNX integration stores its KNXModule under HassKey(DOMAIN),
# i.e. hass.data["knx"]. We deliberately do not import from
# homeassistant.components.knx at module level so a missing or renamed
# built-in integration degrades instead of breaking the import.
KNX_DOMAIN: Final = "knx"

# Options keys
CONF_FEATURES: Final = "features"
CONF_SAFE_MODE: Final = "safe_mode"

# Shared by every file_patch feature - the behaviour belongs to the risk class,
# not to a single patch. One switch, however many patches there are.
CONF_PATCH_AUTO_RESTORE: Final = "patch_auto_restore"

# Issue identifiers for the repair registry
ISSUE_FEATURE_BLOCKED: Final = "feature_blocked"
ISSUE_FEATURE_FAILED: Final = "feature_failed"
ISSUE_SEASON_CONFLICT: Final = "season_ga_conflict"
ISSUE_PATCH_RESTART: Final = "patch_restart_required"


class Category(StrEnum):
    """Grouping shown to the user.

    Diagnostics are read-only and safe. Interworking features change
    behaviour and are therefore always opt-in.
    """

    DIAGNOSTICS = "diagnostics"
    INTERWORKING = "interworking"


class Risk(StrEnum):
    """How invasive a feature is. Drives defaults and the safe-mode switch.

    Risk is a different axis than Category: the category says *what for*, the
    risk says *how deep the intervention goes*. Safe mode switches off
    everything that is not READ_ONLY, so a new class is covered automatically.

    BUS_WRITE is the only class that puts telegrams on the bus itself. That
    deserves its own name rather than hiding under BEHAVIOUR — a user needs to
    see which features can talk to their installation.
    """

    READ_ONLY = "read_only"
    BEHAVIOUR = "behaviour"
    RUNTIME_PATCH = "runtime_patch"
    BUS_WRITE = "bus_write"
    FILE_PATCH = "file_patch"


class FeatureState(StrEnum):
    """Lifecycle state of a single feature.

    Mirrors the vocabulary of the anchor-patch scripts this integration
    replaces (applied / partial / missing / anchors-missing), so the terms
    stay familiar.

    BLOCKED is the important one: the user switched the feature on, but a
    precondition failed, so it was deliberately NOT applied. Silently
    ineffective is the failure mode this integration exists to avoid.
    """

    DISABLED = "disabled"
    ACTIVE = "active"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    FAILED = "failed"
