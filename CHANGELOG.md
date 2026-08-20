# Changelog

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
