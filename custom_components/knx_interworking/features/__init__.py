"""Feature registry and base class.

The registry is the core of this integration: features are declared units
that can be switched on and off individually, must state their own
preconditions, and **must be able to undo themselves**. A feature that
cannot be reverted does not belong here.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from ..const import (
    CONF_FEATURES,
    CONF_SAFE_MODE,
    DOMAIN,
    Category,
    FeatureState,
    Risk,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class FeatureBlocked(Exception):
    """Raised by async_apply when the blocking reason can only be found async.

    check_preconditions() must stay synchronous and non-blocking, so a feature
    whose precondition needs I/O (a subprocess, a network call) reports it from
    async_apply with this exception. The manager treats it exactly like a failed
    precondition: BLOCKED, no rollback, no "please report this" wording.
    """


@dataclass(slots=True)
class Precondition:
    """Result of a precondition check.

    `detail` is shown to the user in the repair issue, so it must explain
    what was expected and what was found — not just "failed".
    """

    ok: bool
    detail: str = ""


class Feature(ABC):
    """A single switchable capability."""

    key: str
    category: Category
    risk: Risk
    default_enabled: bool = False

    #: Whether this feature has to be re-checked while running.
    #: True for runtime hooks - they can be lost without a restart (a reload of
    #: the KNX integration replaces the xknx instance and takes the callbacks
    #: with it). False for file patches: files only change when the image is
    #: replaced, and that always comes with a restart, so checking once at
    #: startup is enough. Polling something that cannot change is pure cost -
    #: for a file patch it would even mean spawning a process.
    heartbeat: bool = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Store context and initialise state."""
        self.hass = hass
        self.entry = entry
        self.state: FeatureState = FeatureState.DISABLED
        self.detail: str = ""
        self._applied_fingerprint: str | None = None

    # --- configuration changes -------------------------------------------
    def config_fingerprint(self) -> str:
        """Stable representation of this feature's own options.

        Used to notice that a feature which is already running was
        reconfigured. Without this, changing an address while the feature is on
        would have no effect until the next restart — and a feature that
        silently keeps using the old configuration is exactly the kind of
        "switched on but not doing what it says" failure this integration
        exists to prevent.
        """
        return json.dumps(
            {key: self.entry.options.get(key) for key in self.option_keys()},
            sort_keys=True,
            default=str,
        )

    def config_changed(self) -> bool:
        """True when the options differ from the ones that were applied."""
        return (
            self._applied_fingerprint is not None
            and self._applied_fingerprint != self.config_fingerprint()
        )

    # --- to be implemented per feature -----------------------------------
    @classmethod
    def options_schema(cls, options: Mapping[str, Any]) -> dict[Any, Any]:
        """Return the feature's own option fields for the options dialog.

        Keys are voluptuous markers, values are selectors. Returning them here
        instead of hard-coding them in the config flow keeps each feature's
        configuration next to its implementation — the flow only groups them.
        Defaults must be taken from ``options`` so the dialog shows what is
        currently stored.
        """
        return {}

    @classmethod
    def option_keys(cls) -> list[str]:
        """Plain option keys of this feature, derived from its schema."""
        return [marker.schema for marker in cls.options_schema({})]

    def check_preconditions(self) -> Precondition:
        """Verify the feature can safely act. Default: always fine."""
        return Precondition(ok=True)

    @abstractmethod
    async def async_apply(self) -> str:
        """Activate the feature. Return a short human-readable detail."""

    @abstractmethod
    async def async_revert(self) -> None:
        """Undo everything async_apply did.

        Must be safe to call when the feature was never applied.
        """

    def is_attached(self) -> bool:
        """Whether the feature is still hooked into the running KNX stack.

        Deliberately *not* "was KNX reloaded?". A reload is only one way to lose
        the hook — the xknx instance can be replaced, a callback list can be
        cleared, another integration can undo a patch. Asking "am I still
        attached?" covers all of them, including the ones we did not foresee.
        Default: features that cannot lose their hook report True.
        """
        return True

    async def async_reattach(self) -> None:
        """Reattach after the hook was lost. Default: revert, then apply again.

        A file patch must override this: reverting would restore a backup that
        may predate a core update, i.e. put old code over new. Applying is
        idempotent, so those features simply apply again.
        """
        await self.async_disable()
        await self.async_enable()

    async def async_report(self) -> dict[str, Any]:
        """Full findings for the service call — recomputed where that is possible.

        The status sensor can only carry a bounded excerpt (attributes are
        rendered into every state write). The service answers the same question
        without a limit, and for the scanning features it re-runs the scan, so a
        user can ask again after changing something instead of restarting.
        """
        return self.extra_state()

    def extra_state(self) -> dict[str, Any]:
        """Optional additional attributes for the status sensor."""
        return {}

    # --- driven by the manager -------------------------------------------
    async def async_enable(self) -> None:
        """Check preconditions, then apply. Never raises."""
        pre = self.check_preconditions()
        if not pre.ok:
            # Log on transition or when the reason changes, not on every retry:
            # the heartbeat re-checks blocked features, and a permanently blocked
            # one must not fill the log every 30 seconds.
            if self.state is not FeatureState.BLOCKED or self.detail != pre.detail:
                _LOGGER.warning(
                    "Feature '%s' is switched on but was NOT applied: %s",
                    self.key,
                    pre.detail,
                )
            self.state = FeatureState.BLOCKED
            self.detail = pre.detail
            return
        try:
            self.detail = await self.async_apply()
            self.state = FeatureState.ACTIVE
            self._applied_fingerprint = self.config_fingerprint()
            _LOGGER.info("Feature '%s' active: %s", self.key, self.detail)
        except FeatureBlocked as err:
            # Same meaning as a failed precondition - just discovered later.
            # Deliberately no rollback: nothing was applied.
            if self.state is not FeatureState.BLOCKED or self.detail != str(err):
                _LOGGER.warning(
                    "Feature '%s' is switched on but was NOT applied: %s", self.key, err
                )
            self.state = FeatureState.BLOCKED
            self.detail = str(err)
        except Exception as err:
            self.state = FeatureState.FAILED
            self.detail = f"{type(err).__name__}: {err}"
            _LOGGER.exception("Feature '%s' failed to apply, rolling back", self.key)
            try:
                await self.async_revert()
            except Exception:
                _LOGGER.exception("Rollback of feature '%s' failed as well", self.key)

    async def async_disable(self) -> None:
        """Revert and mark disabled. Never raises."""
        try:
            await self.async_revert()
        except Exception:
            _LOGGER.exception("Feature '%s' could not be reverted cleanly", self.key)
        self.state = FeatureState.DISABLED
        self.detail = ""
        self._applied_fingerprint = None


@dataclass(slots=True)
class FeatureManager:
    """Owns all features and applies the user's choice."""

    hass: HomeAssistant
    entry: ConfigEntry
    features: dict[str, Feature] = field(default_factory=dict)

    def register(self, feature: Feature) -> None:
        """Add a feature to the registry."""
        self.features[feature.key] = feature

    @property
    def safe_mode(self) -> bool:
        """True while all behaviour-changing features are forced off."""
        return bool(self.entry.options.get(CONF_SAFE_MODE, False))

    def wanted(self, key: str) -> bool:
        """Whether the user wants this feature on, honouring safe mode."""
        feature = self.features[key]
        if self.safe_mode and feature.risk is not Risk.READ_ONLY:
            return False
        selected: dict[str, Any] = self.entry.options.get(CONF_FEATURES, {})
        return bool(selected.get(key, feature.default_enabled))

    async def async_sync(self) -> None:
        """Bring every feature in line with the current options."""
        for key, feature in self.features.items():
            want = self.wanted(key)
            is_on = feature.state in (FeatureState.ACTIVE, FeatureState.DEGRADED)
            if want and not is_on:
                await feature.async_enable()
            elif want and is_on and feature.config_changed():
                # Reconfigured while running: reattach instead of disable+enable.
                # For a runtime hook reattach() *is* disable+enable (unchanged);
                # for a FILE_PATCH feature it is apply-only, so a routine option
                # change no longer reverts Home Assistant core files.
                _LOGGER.info(
                    "Feature '%s' was reconfigured — reapplying", feature.key
                )
                await feature.async_reattach()
            elif not want and feature.state is not FeatureState.DISABLED:
                await feature.async_disable()
        self._sync_issues()

    async def async_verify(self) -> bool:
        """Re-attach features that lost their hook. Returns True if it healed one.

        Runs on a heartbeat, because losing the hook is silent: the KNX
        integration can be reloaded at any time and takes its xknx instance —
        and with it our callback registrations — with it. A bus feature that
        quietly stopped working is worse than one that never started.
        """
        healed = False
        for key, feature in self.features.items():
            if not feature.heartbeat:
                continue  # checked once at setup - nothing can change at runtime
            # A blocked feature is retried: the usual reason is that KNX was not
            # (yet) available, which fixes itself once it is back.
            if feature.state is FeatureState.BLOCKED and self.wanted(key):
                before = feature.state
                await feature.async_enable()
                if feature.state is not before:
                    healed = True
                continue
            if feature.state not in (FeatureState.ACTIVE, FeatureState.DEGRADED):
                continue
            if feature.is_attached():
                continue
            _LOGGER.warning(
                "Feature '%s' lost its hook (most likely the KNX integration was "
                "reloaded, or a core update replaced patched files) — reattaching",
                feature.key,
            )
            await feature.async_reattach()
            healed = True
        if healed:
            self._sync_issues()
        return healed

    async def async_shutdown(self) -> None:
        """Revert everything — used on unload."""
        for feature in self.features.values():
            if feature.state is not FeatureState.DISABLED:
                await feature.async_disable()
        self._clear_issues()

    def summary(self) -> dict[str, Any]:
        """Per-feature state, for the status sensor."""
        return {
            key: {
                "state": f.state.value,
                "category": f.category.value,
                "risk": f.risk.value,
                "detail": f.detail,
                **f.extra_state(),
            }
            for key, f in self.features.items()
        }

    # --- repair issues ----------------------------------------------------
    def _issue_id(self, key: str) -> str:
        return f"{self.entry.entry_id}_{key}"

    def _sync_issues(self) -> None:
        """Surface blocked/failed features as repair issues.

        A log line is not enough: "switched on but ineffective" has to be
        visible in the UI, otherwise it goes unnoticed for months.
        """
        for key, feature in self.features.items():
            issue_id = self._issue_id(key)
            if feature.state is FeatureState.BLOCKED:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="feature_blocked",
                    translation_placeholders={"feature": key, "detail": feature.detail},
                )
            elif feature.state is FeatureState.FAILED:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="feature_failed",
                    translation_placeholders={"feature": key, "detail": feature.detail},
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def _clear_issues(self) -> None:
        for key in self.features:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id(key))
