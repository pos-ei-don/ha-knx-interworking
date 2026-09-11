"""Shared machinery for features that keep a **file patch** in Home Assistant core.

Extracted 2026-09-11, when the KNX cover ``position_state_send`` patch became the
second one managed this way. The first — the climate status text — had all of this
inline; a second copy would have drifted from the one that was proven in practice,
which is exactly what ``climate_status_text`` warns about for the patch *script*.

What is shared, and why it is shared
------------------------------------
Everything a file patch needs is identical between patches: locate the installed
``homeassistant`` package, drive the patch script through its contract
(``<script> <ROOT> [--status|--revert]``, ``--status`` prints exactly one word),
cache the answer because it costs a process, refuse to write when the anchors no
longer match, and ask for a restart through a repair issue instead of pretending
the patch is effective the moment it was written.

What a subclass supplies is only what actually differs:

======================  ====================================================
``key``                 feature key
``script_name``         file under ``patches/`` (its ``.edits.json`` sibling,
                        if any, is found by the script itself)
``title``               short human name, used in log lines
``revert_note``         the consequence of a revert beyond the restart
======================  ====================================================

⚠️ **A file patch only takes effect after a restart** — the module is long since
imported. Hence no polling: files cannot change while Home Assistant runs, only
replacing the image does that, and that always brings a restart with it. The state
is read **once at startup**; ``heartbeat`` stays off.

⚠️ **A file patch leaves a modified installation behind**, even after uninstalling.
That is why every feature of this risk class is **off by default**, and why even
when switched on it only *reports* unless write-back is enabled as well. Nothing
here touches a core file without two explicit decisions by the user.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers import selector

from ..const import CONF_PATCH_AUTO_RESTORE, DOMAIN, ISSUE_PATCH_RESTART, Category, Risk
from . import Feature, FeatureBlocked, Precondition

_LOGGER = logging.getLogger(__name__)

PATCH_DIR = Path(__file__).parent.parent / "patches"

# Status vocabulary of the patch scripts.
STATE_APPLIED = "applied"
STATE_PARTIAL = "partial"
STATE_MISSING = "missing"
STATE_ANCHORS_GONE = "anchors-missing"
STATE_FILE_MISSING = "file-missing"

# The status call spawns a process. It is read once when the feature is enabled;
# the cache only guards against a burst of reconfigurations.
STATUS_CACHE_SECONDS = 300


class FilePatchFeature(Feature):
    """Keep a core file patch applied, and say when it is not."""

    # --- what a subclass must set ----------------------------------------
    script_name: str = ""
    title: str = "file patch"
    revert_note: str = ""

    # --- shared defaults --------------------------------------------------
    category = Category.INTERWORKING
    risk = Risk.FILE_PATCH
    default_enabled = False
    # Files only change when the image is replaced, and that always comes with a
    # restart. Checking once at setup is enough - no polling, and no subprocess
    # every 30 seconds.
    heartbeat = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise state."""
        super().__init__(*args, **kwargs)
        self._status: str | None = None
        self._status_at: float = 0.0
        self._applied_by_us = 0
        self._restart_pending = False
        # Set the instant we start writing files this session (before the write,
        # not after success) so a partial/failed write still gets rolled back.
        self._wrote_this_session = False

    @classmethod
    def script(cls) -> Path:
        """Absolute path of the shipped patch script."""
        return PATCH_DIR / cls.script_name

    # --- configuration ----------------------------------------------------
    @classmethod
    def options_schema(cls, options: Mapping[str, Any]) -> dict[Any, Any]:
        """One choice, shared by every file-patch feature: restore, or just report."""
        return {
            vol.Optional(
                CONF_PATCH_AUTO_RESTORE,
                default=options.get(CONF_PATCH_AUTO_RESTORE, False),
            ): selector.BooleanSelector()
        }

    @property
    def auto_restore(self) -> bool:
        """Whether a missing patch is written back without being asked."""
        return bool(self.entry.options.get(CONF_PATCH_AUTO_RESTORE, False))

    # --- talking to the script --------------------------------------------
    @staticmethod
    def _root() -> Path | None:
        """Directory that contains ``homeassistant/components/knx``.

        Derived from the installed package instead of hard-coded, so this also
        works on installations that do not live in /usr/src/homeassistant.
        """
        try:
            import homeassistant

            root = Path(homeassistant.__file__).resolve().parent.parent
        except Exception:
            return None
        return root if (root / "homeassistant" / "components" / "knx").is_dir() else None

    async def _run(self, *args: str, timeout: float = 60.0) -> tuple[int, str]:
        """Run the patch script. Returns (returncode, stdout+stderr)."""
        root = self._root()
        if root is None:
            return 1, "could not locate the homeassistant package"
        proc = await asyncio.create_subprocess_exec(
            "python3",
            str(self.script()),
            str(root),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)  # reap, but never hang
            except TimeoutError:
                pass  # child is defunct/unreapable — don't block the loop on it
            return 1, f"patch script timed out after {timeout:.0f} s"
        return proc.returncode or 0, out.decode(errors="replace").strip()

    async def _read_status(self, *, force: bool = False) -> str:
        """Current patch state, cached — it costs a process."""
        now = time.monotonic()
        if not force and self._status is not None and now - self._status_at < STATUS_CACHE_SECONDS:
            return self._status
        rc, out = await self._run("--status", timeout=30.0)
        word = out.splitlines()[-1].strip() if out else ""
        if rc != 0 or not word:
            word = STATE_FILE_MISSING
        self._status, self._status_at = word, now
        return word

    # --- preconditions ----------------------------------------------------
    def check_preconditions(self) -> Precondition:
        """Only the cheap, synchronous checks belong here.

        The patch state itself needs a subprocess, so it is evaluated in
        ``async_apply`` — ``check_preconditions`` must stay non-blocking.
        """
        if not self.script().is_file():
            return Precondition(
                ok=False,
                detail=f"Patch script is missing from the integration: {self.script_name}",
            )
        if self._root() is None:
            return Precondition(
                ok=False,
                detail=(
                    "Could not find homeassistant/components/knx next to the installed "
                    "package — this installation layout is not supported."
                ),
            )
        return Precondition(ok=True)

    # --- apply / revert ---------------------------------------------------
    async def async_apply(self) -> str:
        """Report the state; write the patch back only if allowed to."""
        # Read once when the feature comes up. There is no heartbeat for file
        # patches, so this is the check - and it is the only one needed.
        state = await self._read_status(force=self._status is None)

        if state == STATE_APPLIED:
            ir.async_delete_issue(self.hass, DOMAIN, self._restart_issue_id())
            self._restart_pending = False
            return "patch applied"

        if state in (STATE_ANCHORS_GONE, STATE_FILE_MISSING):
            # Not our patch's fault: the target code changed shape. Never write
            # into files we no longer recognise.
            raise FeatureBlocked(
                f"patch state '{state}' — the KNX integration's code no longer matches the "
                "anchors. Not writing anything; the patch needs to be updated for this "
                "Home Assistant version."
            )

        # state is 'missing' or 'partial'
        if not self.auto_restore:
            raise FeatureBlocked(
                f"patch state '{state}' — most likely a core update replaced the files. "
                "Automatic write-back is switched off, so nothing was changed. Switch on "
                "'apply automatically', or run the patch by hand and restart."
            )

        self._wrote_this_session = True  # BEFORE the write: a partial write must revert
        rc, out = await self._run()
        state = await self._read_status(force=True)
        if state != STATE_APPLIED:
            raise RuntimeError(f"applying failed (rc={rc}, state={state}): {out[-300:]}")

        self._applied_by_us += 1
        self._restart_pending = True
        _LOGGER.warning(
            "%s patch was written back. It only takes effect after a restart of Home Assistant",
            self.title,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._restart_issue_id(),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PATCH_RESTART,
            translation_placeholders={"feature": self.key},
        )
        return "patch written back — restart required"

    async def async_reattach(self) -> None:
        """Apply again without reverting first.

        Reverting would restore a backup that predates the core update and put old
        code over new. Applying is idempotent, so it is the only safe direction.
        """
        await self.async_enable()

    async def async_revert(self) -> None:
        """Restore the unpatched files.

        ⚠️ Two consequences the user must know, hence the warning: it needs a
        restart, and whatever ``revert_note`` says about entities that still carry
        the patched configuration.
        """
        ir.async_delete_issue(self.hass, DOMAIN, self._restart_issue_id())
        self._restart_pending = False
        if not self._wrote_this_session:
            # Never revert a patch this integration did not write itself this
            # session — even if the files are currently patched (pre-existing, a
            # previous run, or the user applied it). Reverting foreign/pre-existing
            # state would restore a possibly stale backup over current core files,
            # and a routine option change must never tear out core files. The flag
            # is set *before* the write, so a partial/failed write is still undone.
            self._status = None
            return
        rc, out = await self._run("--revert", timeout=60.0)
        self._status = None
        self._wrote_this_session = False  # our write is undone
        self._applied_by_us = 0  # reset the reporting counter too
        _LOGGER.warning(
            "%s patch reverted (rc=%s). A restart is required. %s %s",
            self.title,
            rc,
            self.revert_note,
            out[-200:],
        )

    def is_attached(self) -> bool:
        """Whether the patch is still in the files (cached value only).

        Note: file-patch features set ``heartbeat = False``, so the manager never
        calls this on the 30-second beat — a file patch cannot silently lose its
        hook the way a runtime wrapper can (only a core update changes files, and
        that forces a restart). It is kept as an explicit, process-free statement
        of intent and for any manual verification. With automatic write-back off we
        report True, because reattaching would write without permission.
        """
        if not self.auto_restore:
            return True
        return self._status in (None, STATE_APPLIED)

    # --- reporting --------------------------------------------------------
    def _restart_issue_id(self) -> str:
        return f"{self.entry.entry_id}_{self.key}_restart"

    def extra_state(self) -> dict[str, Any]:
        """Expose the patch state in the same words the scripts use."""
        return {
            "patch_status": self._status,
            "patch_auto_restore": self.auto_restore,
            "patch_applied_by_integration": self._applied_by_us,
            "patch_restart_pending": self._restart_pending,
            "patch_script": self.script_name,
        }
