# Changelog

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
