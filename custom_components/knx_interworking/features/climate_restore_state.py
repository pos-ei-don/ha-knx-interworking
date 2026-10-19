"""Restore the last state of KNX climate entities after a restart — via file patch.

Why
---
After every restart all KNX climate entities report hvac_mode ``off`` and preset
``standby`` until the state sync has read them from the bus — minutes on a large
installation. Automations and dashboards see a heating that is "off" everywhere.
Core restores the last state for KNX switches since home-assistant/core#176352,
but not for climate. This feature carries the same fix for climate until it is
in core: on/off, controller mode and preset are pre-filled from the last known
state (internally, no telegram), and the first real telegram replaces them.

Why a file patch
----------------
Restoring needs ``RestoreEntity`` as a base of the climate entity class and must
run inside ``async_added_to_hass`` before the device is registered on the bus —
neither can be added from outside at runtime without replacing the class.

⚠️ Home Assistant only stores the last state of entities that are restorable, so
the first restart after enabling the patch has nothing to restore yet; from the
second restart on it takes effect.
"""

from __future__ import annotations

from ._file_patch import FilePatchFeature


class ClimateRestoreStatePatch(FilePatchFeature):
    """Keep the climate restore-state patch applied, and say when it is not."""

    key = "patch_climate_restore_state"
    script_name = "knx_climate_restore_patch.py"
    title = "Climate restore state"
    revert_note = (
        "After the next restart, KNX climate entities show 'off' and 'standby' again "
        "until the bus has been read."
    )
