#!/usr/bin/env python3
"""KNX climate: restore the last state after a Home Assistant restart, until the bus answers.

After every restart all KNX climate entities reported hvac_mode `off` and preset `standby` for minutes - until the
state sync of a large bus had read them (6 minutes measured). Cause: `_KnxClimate` has no RestoreEntity part; before
the first telegram `xknx.Climate.on.value` is None (so `is_on` is False and the mode OFF) and
`ClimateMode.operation_mode` holds the xknx default STANDBY. The same issue existed for the KNX switch and is fixed
there by home-assistant/core#176352 (restore the last state). This patch does the same for climate: on/off,
controller mode and preset (operation mode) are pre-filled from the last state - internally only (RemoteValue.value
and the ClimateMode attributes send no telegram), before the device is registered on the bus; the first real
telegram overwrites them. The target temperature is deliberately not restored (inconsistent with setpoint shift).

Anchors are matched tolerantly and the result is checked before writing (see _anchor.py). Coexists with the
status-text patch on the same file: --revert takes back exactly these edits (reverse replacement, no backup copy),
so the other patch stays untouched. A backup (.knxrestore.bak) is still written when applying.

    python3 knx_climate_restore_patch.py <ROOT> [--check|--revert|--status|--orphans]
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _anchor  # noqa: E402  (tolerant anchors + safety check, see there)

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
MODE = next((a for a in sys.argv[2:] if a.startswith("--")), "")
KNX = ROOT / "homeassistant" / "components" / "knx"
REL = "climate.py"
SUFFIX = ".knxrestore.bak"
MARKER = "def _restore_last_state("

RESTORE_METHOD = '''    def _restore_last_state(self, last_state: State) -> None:
        """Restore on/off and modes until the first telegram is received."""
        if last_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        try:
            hvac_mode = HVACMode(last_state.state)
        except ValueError:
            return
        if self._device.supports_on_off and self._device.on.value is None:
            self._device.on.value = hvac_mode != HVACMode.OFF
        if hvac_mode != HVACMode.OFF:
            self._last_hvac_mode = hvac_mode
        if (mode := self._device.mode) is None:
            return
        if mode.supports_controller_mode and (
            hvac_mode != HVACMode.OFF or not self._device.supports_on_off
        ):
            if (knx_controller_mode := CONTROLLER_MODES_INV.get(hvac_mode)) is not None:
                mode.controller_mode = knx_controller_mode
        if mode.supports_operation_mode and (
            preset_mode := last_state.attributes.get(ATTR_PRESET_MODE)
        ):
            # inverse of the preset_mode property: operation_mode.name.lower()
            if operation_mode := HVACOperationMode.__members__.get(
                str(preset_mode).upper()
            ):
                mode.operation_mode = operation_mode

'''

EDITS: list[tuple[str, str]] = [
    ("from homeassistant.components.climate import (\n    FAN_HIGH,",
     "from homeassistant.components.climate import (\n    ATTR_PRESET_MODE,\n    FAN_HIGH,"),
    ("from homeassistant.const import ATTR_TEMPERATURE, CONF_NAME, Platform, UnitOfTemperature\n",
     "from homeassistant.const import (\n    ATTR_TEMPERATURE,\n    CONF_NAME,\n    STATE_UNAVAILABLE,\n"
     "    STATE_UNKNOWN,\n    Platform,\n    UnitOfTemperature,\n)\n"),
    ("from homeassistant.core import HomeAssistant\n",
     "from homeassistant.core import HomeAssistant, State\n"),
    ("from homeassistant.helpers.typing import ConfigType\n",
     "from homeassistant.helpers.restore_state import RestoreEntity\nfrom homeassistant.helpers.typing import ConfigType\n"),
    ("class _KnxClimate(ClimateEntity, _KnxEntityBase):",
     "class _KnxClimate(ClimateEntity, _KnxEntityBase, RestoreEntity):"),
    # before super(): the device is not registered on the bus yet, nothing received gets overwritten
    ('        """Store register state change callback and start device object."""\n'
     "        await super().async_added_to_hass()\n",
     '        """Store register state change callback and start device object."""\n'
     "        if (last_state := await self.async_get_last_state()) is not None:\n"
     "            self._restore_last_state(last_state)\n"
     "        await super().async_added_to_hass()\n"),
    ("    @override\n    async def async_added_to_hass(self) -> None:\n"
     '        """Store register state change callback and start device object."""\n',
     RESTORE_METHOD + "    @override\n    async def async_added_to_hass(self) -> None:\n"
     '        """Store register state change callback and start device object."""\n'),
]

KNOWN_MODES = ("", "--check", "--revert", "--status", "--orphans")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    shutil.copymode(path, tmp)
    os.replace(tmp, path)


def plan() -> tuple[str | None, str | None, list[str], list[str]]:
    """(before, after, anchor problems, safety problems) - nothing is written here."""
    f = KNX / REL
    if not f.exists():
        return None, None, [f"{REL}: file missing"], []
    before = f.read_text(encoding="utf-8")
    after, problems = _anchor.apply_hunks(before, [list(e) for e in EDITS])
    anchors = [f"{REL}: {p}" for p in problems]
    unsafe = _anchor.check_source(REL, before, after) if not problems and after != before else []
    return before, after, anchors, unsafe


def status() -> str:
    """One word, rc always 0: applied | partial | missing | anchors-missing | incompatible | file-missing."""
    f = KNX / REL
    if not f.exists():
        return "file-missing"
    t = f.read_text(encoding="utf-8")
    states = [_anchor.hunk_state(t, list(e))[0] for e in EDITS]
    if all(st == _anchor.STATE_APPLIED for st in states):
        return "applied"
    if MARKER in t or _anchor.STATE_APPLIED in states:
        return "partial"
    if _anchor.STATE_GONE in states:
        return "anchors-missing"
    _b, _a, anchors, unsafe = plan()
    if anchors:
        return "anchors-missing"
    return "incompatible" if unsafe else "missing"


def main() -> int:
    if MODE not in KNOWN_MODES:
        print(f"unknown mode: {MODE}  (allowed: {' '.join(m or '<empty>=apply' for m in KNOWN_MODES)})")
        return 1
    f = KNX / REL
    if MODE == "--status":
        print(status())
        return 0
    if MODE == "--orphans":
        bak = Path(str(f) + SUFFIX)
        print(f"  ORPHAN: {REL}{SUFFIX}" if bak.exists() and not f.exists() else "no orphaned backups")
        return 0
    if MODE == "--revert":
        if not f.exists():
            print(f"  missing: {f}")
            return 2
        t = f.read_text(encoding="utf-8")
        if MARKER not in t:
            print(f"  ~ {REL}: patch not applied")
            return 0
        if any(neu not in t for _, neu in EDITS):
            print(f"  ~ {REL}: edits no longer as applied - NOT reverted")
            return 2
        for alt, neu in reversed(EDITS):
            t = t.replace(neu, alt, 1)
        _atomic_write(f, t)
        print(f"  reverted: {REL} (only these edits) - restart Home Assistant.")
        return 0
    before, after, anchors, unsafe = plan()
    if anchors:
        print("  ANCHORS MISSING - nothing written:")
        for line in anchors:
            print(f"      {line}")
        return 2
    if unsafe:
        print("  INCOMPATIBLE - the place matches, the patch code no longer does; nothing written:")
        for line in unsafe:
            print(f"      {line}")
        return 3
    if after == before:
        print(f"  ~ {REL}: already patched")
        return 0
    print(f"  ok {REL}: anchors found, check passed")
    if MODE == "--check":
        print("--check: nothing written")
        return 0
    shutil.copy(f, Path(str(f) + SUFFIX))
    _atomic_write(f, after)
    print(f"  + {REL} (backup: {REL}{SUFFIX})")
    ok, msg = _anchor.import_probe(ROOT, [REL])
    print(f"  {msg}")
    if ok is False:
        _atomic_write(f, before)
        print("INCOMPATIBLE - original restored, no restart needed.")
        return 3
    print("done - restart Home Assistant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
