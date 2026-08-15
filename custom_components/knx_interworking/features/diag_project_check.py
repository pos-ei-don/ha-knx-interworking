"""Compare what Home Assistant reads with what the ETS project actually offers.

Two findings, both from the parsed ETS project (``.storage/knx/knx_project.json``)
against the running devices:

**Asked in vain** — Home Assistant sends a GroupValueRead at startup for every
state address with ``sync_state``. If no communication object on that address
carries the **read flag**, nobody will ever answer, and the request runs into a
timeout. Measured in the reference installation: **107 of 522 addresses**. Those
are the "KNX bus did not respond in time" warnings that scroll past at every
start — with this, they have names.

**Not in the project** — an address configured in Home Assistant that no object
in the ETS project uses. Usually a typo or a leftover from a device that was
removed in ETS but not here.

Read-only, computed once at setup: the answer changes only when the project or
the configuration changes, and both mean a reload. No polling.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..const import Category, Risk
from . import Feature, Precondition
from ._ga_scan import readable_addresses, scan

_LOGGER = logging.getLogger(__name__)

PROJECT_FILE = Path("/config/.storage/knx/knx_project.json")
MAX_REPORTED = 30


class ProjectCheck(Feature):
    """Cross-check the KNX configuration against the imported ETS project."""

    key = "diag_project_check"
    category = Category.DIAGNOSTICS
    risk = Risk.READ_ONLY
    default_enabled = False
    heartbeat = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise the result store."""
        super().__init__(*args, **kwargs)
        self._unanswerable: dict[str, str] = {}
        self._unknown: dict[str, str] = {}
        self._checked = 0

    def check_preconditions(self) -> Precondition:
        """Needs a running KNX and an imported ETS project."""
        knx = self.hass.data.get("knx")
        if getattr(knx, "xknx", None) is None:
            return Precondition(ok=False, detail="KNX is not running — nothing to inspect.")
        if not PROJECT_FILE.is_file():
            return Precondition(
                ok=False,
                detail=(
                    "No ETS project imported. Upload the .knxproj in the KNX panel — "
                    "without it there is nothing to compare against."
                ),
            )
        return Precondition(ok=True)

    async def async_apply(self) -> str:
        """Read the project and compare it with the configured addresses."""
        return await self._compute()

    async def async_report(self) -> dict[str, Any]:
        """Re-read the project, then return every finding without a limit."""
        await self._compute()
        return {
            "read_addresses": self._checked,
            "unanswerable_count": len(self._unanswerable),
            "unanswerable": self._unanswerable,
            "unknown_count": len(self._unknown),
            "unknown": self._unknown,
        }

    async def _compute(self) -> str:
        project = await self.hass.async_add_executor_job(self._load_project)
        if project is None:
            raise RuntimeError("could not read the imported ETS project")

        objects = project.get("communication_objects") or {}
        # Which addresses can actually answer a read request?
        answerable: set[str] = set()
        known: set[str] = set()
        for obj in objects.values():
            flags = obj.get("flags") or {}
            for addr in obj.get("group_address_links") or []:
                known.add(str(addr))
                if flags.get("read"):
                    answerable.add(str(addr))

        xknx = self.hass.data["knx"].xknx
        reads = readable_addresses(xknx)
        self._checked = len(reads)
        self._unanswerable = {a: who for a, who in reads.items() if a in known and a not in answerable}
        # Only meaningful if the project actually lists addresses.
        self._unknown = {
            a: (u.writers + u.readers)[0] if (u.writers or u.readers) else "?"
            for a, u in scan(xknx).items()
            if known and a not in known
        }

        if self._unanswerable:
            _LOGGER.warning(
                "%d of %d group addresses are read at startup although no object has the "
                "read flag — every one of them runs into a timeout. First: %s",
                len(self._unanswerable),
                self._checked,
                ", ".join(list(self._unanswerable)[:5]),
            )
        return (
            f"{len(self._unanswerable)} of {self._checked} read addresses cannot answer, "
            f"{len(self._unknown)} address(es) not in the project"
        )

    async def async_revert(self) -> None:
        """Nothing was changed."""
        self._unanswerable = {}
        self._unknown = {}

    @staticmethod
    def _load_project() -> dict[str, Any] | None:
        try:
            raw = json.loads(PROJECT_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _LOGGER.exception("Could not read %s", PROJECT_FILE)
            return None
        return raw.get("data", raw)

    def extra_state(self) -> dict[str, Any]:
        """The two lists, bounded."""
        return {
            "project_read_addresses": self._checked,
            "project_unanswerable_count": len(self._unanswerable),
            "project_unanswerable": dict(list(self._unanswerable.items())[:MAX_REPORTED]),
            "project_unknown_count": len(self._unknown),
            "project_unknown": dict(list(self._unknown.items())[:MAX_REPORTED]),
        }
