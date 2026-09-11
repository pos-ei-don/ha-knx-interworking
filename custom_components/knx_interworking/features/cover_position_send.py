"""KNX cover: actively send the calculated position — via file patch.

For actuators that do **not** report their position, Home Assistant's
``TravelCalculator`` is the only source of truth. Displays (glass push-buttons)
can therefore only show the travel progress if HA publishes the value. Without
the patch that is only possible through ``expose`` — with hand-written
inversion, a hand-picked DPT and the risk of writing onto the entity's own
``ga_position_state``, which is a loop that has been observed in practice.

The patch adds a switch next to the existing position-status field:

    YAML: ``position_state_send: true``
    UI:   "Actively send the calculated position", right below "Current position"

One field for one address; the switch turns its direction around. When it is
set, the address is no longer registered as an xknx ``position_state`` — HA
stops listening there, which makes the loop **structurally** impossible instead
of merely documented away.

Why this is a file patch and not a runtime hook
-----------------------------------------------
Same reason as the climate status text: the **configuration field in the KNX
entity dialog** cannot be added from outside. ``ENTITY_STORE_DATA_SCHEMA`` is
built at import time by value, and its labels live in the *knx* integration's
translations, which a custom integration cannot extend.

Upstream
--------
This is the local carrier of `home-assistant/core#178222
<https://github.com/home-assistant/core/pull/178222>`_. That pull request is
**open**, not merged (last activity 2026-08-06), and Home Assistant 2026.9.1
carries no ``position_state_send``. Once it is merged the patch will report
``applied`` on its own — the state is checked against the replacement text, not
against a marker — and this feature can be switched off and removed.

⚠️ **While the patch is missing, do not save an affected cover in the KNX panel.**
The entity store validates with ``extra=REMOVE_EXTRA``: an unknown
``position_state_send`` is dropped **silently** on save, and the flag is gone from
the stored configuration without any message. Covers that already carry the flag
keep it as long as nobody saves them.
"""

from __future__ import annotations

from ._file_patch import FilePatchFeature


class CoverPositionSendPatch(FilePatchFeature):
    """Keep the cover position-send patch applied, and say when it is not."""

    key = "patch_cover_position_send"
    script_name = "knx_cover_position_patch.py"
    title = "Cover position-send"
    #: ⚠️ Beyond the restart: covers that have ``position_state_send`` stored
    #: lose the flag the next time they are saved in the KNX panel, because the
    #: schema no longer knows the key and drops unknown keys silently.
    revert_note = (
        "Covers that have 'position_state_send' stored will silently lose the "
        "flag the next time they are saved in the KNX panel."
    )
