"""Catalogue of available features.

Kept separate from the registry so both the setup and the config flow can
enumerate features without importing each other.
"""

from __future__ import annotations

from . import Feature
from .climate_command_delay import ClimateCommandDelay
from .climate_status_text import ClimateStatusTextPatch
from .diag_decode_errors import DecodeErrorMonitor
from .diag_ga_conflicts import DptConflictCheck, DuplicateWriterCheck
from .diag_project_check import ProjectCheck
from .reserved_bits import ReservedBitMasking
from .season_bit import SeasonBitSender

# Order matters: it is the order shown in the options dialog.
FEATURE_CLASSES: tuple[type[Feature], ...] = (
    # Diagnostics first - they only read and are the ones a user should try first.
    DecodeErrorMonitor,
    DptConflictCheck,
    DuplicateWriterCheck,
    ProjectCheck,
    # Then the ones that change something.
    ReservedBitMasking,
    SeasonBitSender,
    ClimateCommandDelay,
    ClimateStatusTextPatch,
)

__all__ = [
    "FEATURE_CLASSES",
    "ClimateCommandDelay",
    "ClimateStatusTextPatch",
    "DecodeErrorMonitor",
    "DptConflictCheck",
    "DuplicateWriterCheck",
    "ProjectCheck",
    "ReservedBitMasking",
    "SeasonBitSender",
]
