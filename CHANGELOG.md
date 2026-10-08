# Changelog

## 0.8.1 — 2026-10-08

Compatibility with Home Assistant 2026.10, and file patches that survive cosmetic upstream changes.

- **Both file patches work again on Home Assistant 2026.10.** 2026.10 moved the KNX YAML schemas from
  `voluptuous` to `probatio` and hands UI entities a typed `KnxEntityData` instead of a dict. Both
  patches reported `anchors-missing` and wrote nothing. They now carry a version of the affected
  code for 2026.10 and keep the one for 2026.9, and pick the one that fits the installed code.
- **Anchors are matched tolerantly.** Whitespace, line breaks, comments and the `vol`/`probatio`
  alias no longer break an anchor; inserted code is written with the alias the file imports. The
  lines that change must still match exactly, the surrounding context only as far as needed — and
  only if the place is unique. A tie or too little matching context writes nothing.
- **New safety check before anything is written.** A loose anchor finds the place, but cannot tell
  whether the code still fits. Every patched file must now compile and must not use a name the file
  no longer defines; after writing, the modules are imported in a separate interpreter and the
  originals are restored if that fails. A patch that fails reports the new state **`incompatible`**
  and the feature stays blocked instead of breaking the KNX integration at the next restart. On
  2026.10 this check would have caught the old code of both patches.
- Tested against Home Assistant 2026.10.0: the full KNX test suite passes with both patches applied
  (the only differences are the two new fields in the UI schema snapshots), plus 8 tests for the
  patched behaviour and 9 unit tests for the matching. Applying on 2026.9.4 still selects the 2026.9
  code. Reverting leaves the core files byte-identical.

## 0.8.0 — 2026-09-11

New opt-in interworking feature, and the file-patch machinery is now shared.

- New **Cover: actively send the calculated position** (`position_state_send`). For actuators that
  do not report their position, Home Assistant's travel calculator is the only source — displays
  such as glass push-buttons can therefore only show the travel if Home Assistant publishes it.
  The patch adds a switch next to the existing *Current position* field in the KNX entity dialog:
  when it is set, that address is published to instead of listened on, so the entity can no longer
  read its own telegram back as actuator feedback. Off by default, like every file patch here.
  It carries [home-assistant/core#178222](https://github.com/home-assistant/core/pull/178222),
  which is still open — once that is merged the patch reports itself as applied and the feature
  can be switched off.
- The patch publishes the restored position **once** at startup. It could go out twice, about a
  cooldown apart: xknx's `ExposeSensor` cooldown task compares against the remote value's
  `last_payload`, which is only updated once the outgoing telegram has been processed — during
  startup that can lag past the cooldown. Fixed upstream in the same pull request.
- The `convert_yaml` action now has a name and a description. It was missing from every
  translation, so it showed up in Developer Tools → Actions as a bare key.
- Internal: both file-patch features now share one base class (`features/_file_patch.py`). A
  feature supplies its key, its script and one sentence about reverting; everything else — running
  the script, caching the status, the restart repair issue, refusing to write when the anchors no
  longer match — lives in one place. No behaviour change for *Climate status text*.

## 0.7.1 — 2026-09-06

Compatibility with Home Assistant 2026.9.1.

- The **Climate status text** file-patch is re-anchored for HA 2026.9.1. That release migrated the
  KNX entity-store schema (`components/knx/storage/entity_store_schema.py`) from `voluptuous` to
  `probatio` — a drop-in-compatible validation library — which moved the anchor the patch relies on.
  After updating to 2026.9.1 the patch shows as *missing* until re-applied: open **Settings → Updates**,
  press **Install** on the KNX Interworking patch entry, then restart Home Assistant.
- Verified unaffected on 2026.9.1 (xknx 3.20.0), no changes needed: reserved-bit masking (the
  `GroupAddressDPT.set_decoded_data` hook is unchanged), Climate command delay, and the diagnostic
  features.

## 0.7.0 — 2026-08-20

- New opt-in interworking feature **Climate command delay**: for HVAC actuators that switch
  themselves off when Home Assistant writes the mode and the on/off command back to back. It
  watches the mode write and drives a separate on/off address a configurable delay (default
  100 ms) later. Off by default; you name the mode address, the on/off address and the delay,
  and take the on/off address out of the climate entity so only this drives it.

## 0.6.1 — 2026-08-16
Hardening from an additional external code review; no behaviour change.

- Patch writes are now atomic (temp file + rename), and `--revert` no longer restores
  a stale backup over files a core update has already changed.
- The decode-error diagnostic bounds its memory (caps tracked addresses and raw values).
- Smaller robustness fixes: the patch subprocess can't hang on a timeout, and the
  30-second heartbeat can no longer overlap its own reattach run.

## 0.6.0 — 2026-08-15
First public release.

- **Diagnostics** (read-only): ETS project check, decode-error monitor, DPT-conflict
  and duplicate-writer detection — run automatically or on demand via the
  `knx_interworking.run_check` action.
- **Opt-in interworking fixes** (off by default, logged when they act): reserved-bit
  masking for small payloads (DPT 1/2/3), a summer/winter bit sent alongside the KNX
  time server, and a climate `status_text` field.
- Per-feature enable/disable with a safe-mode kill switch and a repair issue whenever
  a feature is switched on but cannot act.
