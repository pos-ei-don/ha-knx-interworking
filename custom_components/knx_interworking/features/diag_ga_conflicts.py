"""Two checks on the same data: DPT conflicts and duplicate writers.

Both answer "do two parts of this installation disagree about one group
address?", both from one walk over the running devices (see ``_ga_scan``), and
both are switchable on their own because they mean different things to a user.

**DPT conflict** — one address used with two different datapoint types. Real
case in the reference installation: ``select.example_select`` sent one byte
on ``2/0/104`` and read one bit from ``2/0/101``; the result was
``Payload invalid`` at every start.

**Duplicate writer** — one address written by more than one place. Real case:
the KNX time server and three UI entities all write ``0/0/250``/``251``/``252``.
Harmless today, but the same class of fault as two devices sending the season
bit, which is the one that bites at the daylight-saving changeover.

Both are read-only and computed once when the feature comes up: the answer can
only change when the configuration changes, and that means a reload. Hence
``heartbeat = False`` — no polling.
"""

from __future__ import annotations

import logging
from typing import Any

from ..const import Category, Risk
from . import Feature, Precondition
from ._ga_scan import scan

_LOGGER = logging.getLogger(__name__)

MAX_REPORTED = 25


class _GaScanFeature(Feature):
    """Shared plumbing: one scan, computed at setup, no polling."""

    category = Category.DIAGNOSTICS
    risk = Risk.READ_ONLY
    default_enabled = False
    heartbeat = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialise the result store."""
        super().__init__(*args, **kwargs)
        self._findings: dict[str, Any] = {}

    def check_preconditions(self) -> Precondition:
        """Needs a running xknx with devices."""
        knx = self.hass.data.get("knx")
        if getattr(knx, "xknx", None) is None:
            return Precondition(ok=False, detail="KNX is not running — nothing to inspect.")
        return Precondition(ok=True)

    async def async_revert(self) -> None:
        """Nothing was changed, so nothing has to be undone."""
        self._findings = {}


class DptConflictCheck(_GaScanFeature):
    """Group addresses used with more than one datapoint type."""

    key = "diag_dpt_conflicts"

    async def async_apply(self) -> str:
        """Scan once and keep the findings."""
        return self._compute()

    async def async_report(self) -> dict[str, Any]:
        """Re-scan, then return every finding without a limit."""
        self._compute()
        return {"count": len(self._findings), "findings": self._findings}

    def _compute(self) -> str:
        uses = scan(self.hass.data["knx"].xknx)
        self._findings = {
            addr: use.dpts for addr, use in uses.items() if len(use.dpts) > 1
        }
        if self._findings:
            for addr, dpts in list(self._findings.items())[:5]:
                _LOGGER.warning(
                    "Group address %s is used with %d different datapoint types: %s",
                    addr,
                    len(dpts),
                    ", ".join(f"{d} ({len(u)}x)" for d, u in dpts.items()),
                )
        return (
            f"{len(self._findings)} address(es) with conflicting datapoint types"
            if self._findings
            else "no datapoint-type conflicts found"
        )

    def extra_state(self) -> dict[str, Any]:
        """List the conflicting addresses and who uses them how."""
        items = list(self._findings.items())[:MAX_REPORTED]
        return {
            "dpt_conflicts_count": len(self._findings),
            "dpt_conflicts": {a: d for a, d in items},
            "truncated": max(0, len(self._findings) - MAX_REPORTED),
        }


class DuplicateWriterCheck(_GaScanFeature):
    """Group addresses written from more than one place inside Home Assistant."""

    key = "diag_duplicate_writers"

    async def async_apply(self) -> str:
        """Scan once and keep the findings."""
        return self._compute()

    async def async_report(self) -> dict[str, Any]:
        """Re-scan, then return every finding without a limit."""
        self._compute()
        return {"count": len(self._findings), "findings": self._findings}

    def _compute(self) -> str:
        uses = scan(self.hass.data["knx"].xknx)
        self._findings = {
            addr: sorted(set(use.writers))
            for addr, use in uses.items()
            if len(set(use.writers)) > 1
        }
        if self._findings:
            for addr, who in list(self._findings.items())[:5]:
                _LOGGER.warning(
                    "Group address %s is written by %d places: %s",
                    addr,
                    len(who),
                    "; ".join(who),
                )
        return (
            f"{len(self._findings)} address(es) written from more than one place"
            if self._findings
            else "no address is written twice"
        )

    def extra_state(self) -> dict[str, Any]:
        """List the addresses and their writers."""
        items = list(self._findings.items())[:MAX_REPORTED]
        return {
            "duplicate_writer_count": len(self._findings),
            "duplicate_writers": {a: w for a, w in items},
            "truncated": max(0, len(self._findings) - MAX_REPORTED),
        }
