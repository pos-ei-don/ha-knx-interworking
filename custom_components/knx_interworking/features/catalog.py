"""Catalogue of available features.

Kept separate from the registry so both the setup and the config flow can
enumerate features without importing each other.
"""

from __future__ import annotations

from . import Feature
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
)

__all__ = [
    "FEATURE_CLASSES",
    "DecodeErrorMonitor",
    "DptConflictCheck",
    "DuplicateWriterCheck",
    "ProjectCheck",
    "ReservedBitMasking",
    "SeasonBitSender",
]
