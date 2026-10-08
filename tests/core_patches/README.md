# Tests for the core file patches

These tests exercise the **patched** KNX integration, so they run inside a Home Assistant
core checkout, not in this repository.

```bash
git clone --depth 1 --branch <ha-version> https://github.com/home-assistant/core.git
cd core   # set up the dev environment as described by Home Assistant
python <this repo>/custom_components/knx_interworking/patches/knx_status_text_patch.py .
python <this repo>/custom_components/knx_interworking/patches/knx_cover_position_patch.py .
cp <this repo>/tests/core_patches/test_knx_patch_*.py tests/components/knx/
python -m pytest tests/components/knx
```

Expected: everything passes except `test_websocket.py::test_knx_get_schema[cover]` and
`[climate]` — their snapshots do not know the two fields the patches add to the UI schema.
Revert with `--revert` afterwards; the core files are then byte-identical again.

Run the scripts with the checkout's Python: the import check after writing needs an
interpreter that can import Home Assistant (it is skipped otherwise).

Last run: Home Assistant 2026.10.0 — 656 passed, 2 expected snapshot differences.
