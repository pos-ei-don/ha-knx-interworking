# Changelog

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
