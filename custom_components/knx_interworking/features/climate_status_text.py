"""Climate status text (DPT 16.x) as a `status_text` attribute — via file patch.

Why a file patch and not a runtime wrap
---------------------------------------
The attribute itself could be added by wrapping ``_KnxClimate.extra_state_attributes``
at runtime. What cannot be done that way is the **configuration field in the KNX
entity dialog**, and that field is the comfortable part the user asked to keep:

* ``ENTITY_STORE_DATA_SCHEMA`` is built at import time by a dict comprehension over
  ``KNX_SCHEMA_FOR_PLATFORM`` — it holds the climate schema **by value**, not by
  lookup. Adding a key at runtime means rebuilding two objects, one of them a
  nested ``cv.key_value_schemas`` composition.
* The label of a new key lives in the **knx** integration's translations, which a
  custom integration cannot extend — the field would render unlabeled.
* That very validation layer is being replaced upstream (voluptuous → probatio,
  home-assistant/core#176855), so runtime schema surgery would likely break.

So the file patch stays, and this feature does what the four watcher automations,
four `command_line` sensors and four `shell_command`s did before: know its state,
say it out loud, and put it back after a core update.

Everything mechanical about that — running the script, caching the status, the
restart repair issue, refusing to write when the anchors are gone — lives in
:mod:`._file_patch` since 2026-09-11, when the cover position patch became the
second feature of this kind. This module names the script and the one consequence
that is specific to *this* patch.
"""

from __future__ import annotations

from ._file_patch import FilePatchFeature


class ClimateStatusTextPatch(FilePatchFeature):
    """Keep the climate status-text patch applied, and say when it is not."""

    key = "patch_climate_status_text"
    script_name = "knx_status_text_patch.py"
    title = "Climate status-text"
    #: ⚠️ The consequence of a revert beyond the restart: any climate entity that
    #: has ``ga_status_text`` stored will fail validation afterwards, because the
    #: schema no longer knows that key.
    revert_note = (
        "Climate entities that have a status-text group address stored will not "
        "validate until the patch is applied again."
    )
