#!/usr/bin/env python3
"""KNX climate: optionaler Diagnose-Statustext (DPT 16.x) als Attribut `status_text`.

Anker-basiert und damit versionsunabhaengig — laeuft sowohl auf einem core-Klon als
auch auf der installierten Instanz im HA-Container (/usr/src/homeassistant).

    python3 apply_patch.py <ROOT> [--check|--revert|--status|--orphans]

    ROOT = Verzeichnis, das `homeassistant/components/knx/` enthaelt.
    --check   nur Anker pruefen, nichts schreiben
    --revert  Backups (.knxstatus.bak) zurueckspielen

Design: zweites xknx-Device (xknx.devices.Sensor, value_type latin_1 = DPT 16.001),
angemeldet wie das bestehende ClimateMode-Sub-Device. Kein xknx-Patch noetig.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
MODE = next((a for a in sys.argv[2:] if a.startswith("--")), "")
KNX = ROOT / "homeassistant" / "components" / "knx"
SUFFIX = ".knxstatus.bak"

EDITS: dict[str, tuple[str, list[tuple[str, str]]]] = {}

EDITS["storage/const.py"] = ("CONF_GA_STATUS_TEXT", [(
    'CONF_GA_HUMIDITY_CURRENT: Final = "ga_humidity_current"',
    'CONF_GA_HUMIDITY_CURRENT: Final = "ga_humidity_current"\n'
    'CONF_GA_STATUS_TEXT: Final = "ga_status_text"',
)])

EDITS["storage/entity_store_schema.py"] = ("CONF_GA_STATUS_TEXT", [
    ("    CONF_GA_SETPOINT_SHIFT,\n", "    CONF_GA_SETPOINT_SHIFT,\n    CONF_GA_STATUS_TEXT,\n"),
    ("""        vol.Optional(CONF_GA_HUMIDITY_CURRENT): GASelector(
            write=False, valid_dpt="9.007"
        ),""",
     """        vol.Optional(CONF_GA_HUMIDITY_CURRENT): GASelector(
            write=False, valid_dpt="9.007"
        ),
        vol.Optional(CONF_GA_STATUS_TEXT): GASelector(
            write=False,
            state_required=True,
            passive=False,
            valid_dpt=("16.000", "16.001"),
        ),"""),
])

EDITS["schema.py"] = ("CONF_STATUS_TEXT_STATE_ADDRESS", [
    ('    CONF_HUMIDITY_STATE_ADDRESS = "humidity_state_address"',
     '    CONF_HUMIDITY_STATE_ADDRESS = "humidity_state_address"\n'
     '    CONF_STATUS_TEXT_STATE_ADDRESS = "status_text_state_address"\n'
     '    CONF_STATUS_TEXT_TYPE = "status_text_type"'),
    ("                vol.Optional(CONF_HUMIDITY_STATE_ADDRESS): ga_list_validator,\n",
     "                vol.Optional(CONF_HUMIDITY_STATE_ADDRESS): ga_list_validator,\n"
     "                vol.Optional(CONF_STATUS_TEXT_STATE_ADDRESS): ga_list_validator,\n"
     '                vol.Optional(CONF_STATUS_TEXT_TYPE, default="latin_1"): vol.In(\n'
     '                    ("string", "latin_1")\n'
     "                ),\n"),
])

EDITS["climate.py"] = ("ATTR_STATUS_TEXT", [
    ("""from xknx.devices import (
    Climate as XknxClimate,
    ClimateMode as XknxClimateMode,
    Device as XknxDevice,
)""",
     """from xknx.devices import (
    Climate as XknxClimate,
    ClimateMode as XknxClimateMode,
    Device as XknxDevice,
    Sensor as XknxSensor,
)"""),
    ("    CONF_GA_SETPOINT_SHIFT,\n", "    CONF_GA_SETPOINT_SHIFT,\n    CONF_GA_STATUS_TEXT,\n"),
    ('ATTR_COMMAND_VALUE = "command_value"',
     'ATTR_COMMAND_VALUE = "command_value"\nATTR_STATUS_TEXT = "status_text"'),
    ("def _create_climate_ui(xknx: XKNX, conf: ConfigExtractor, name: str) -> XknxClimate:",
     '''def _create_status_text(
    xknx: XKNX, name: str, group_address_state: Any, value_type: str
) -> XknxSensor:
    """Return a KNX Sensor device for the climate's diagnostic status text.

    A separate xknx device, handled like the ClimateMode sub-device: registered in
    async_added_to_hass and added to xknx.devices there.
    """
    return XknxSensor(
        xknx,
        name=f"{name} Status Text",
        group_address_state=group_address_state,
        always_callback=True,
        value_type=value_type,
    )


def _create_climate_ui(xknx: XKNX, conf: ConfigExtractor, name: str) -> XknxClimate:'''),
    ("""    _device: XknxClimate
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "knx_climate\"""",
     """    _device: XknxClimate
    _status_text: XknxSensor | None = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_translation_key = "knx_climate\""""),
    ("""        if self._device.command_value.initialized:
            attr[ATTR_COMMAND_VALUE] = self._device.command_value.value
        return attr""",
     """        if self._device.command_value.initialized:
            attr[ATTR_COMMAND_VALUE] = self._device.command_value.value
        if self._status_text is not None and (
            status_text := self._status_text.resolve_state()
        ) is not None:
            attr[ATTR_STATUS_TEXT] = status_text
        return attr"""),
    ("""        if self._device.mode is not None:
            self._device.mode.register_device_updated_cb(self.after_update_callback)
            self._device.mode.xknx.devices.async_add(self._device.mode)""",
     """        if self._device.mode is not None:
            self._device.mode.register_device_updated_cb(self.after_update_callback)
            self._device.mode.xknx.devices.async_add(self._device.mode)
        if self._status_text is not None:
            self._status_text.register_device_updated_cb(self.after_update_callback)
            self._status_text.xknx.devices.async_add(self._status_text)"""),
    ("""        if self._device.mode is not None:
            self._device.mode.unregister_device_updated_cb(self.after_update_callback)
            self._device.mode.xknx.devices.async_remove(self._device.mode)""",
     """        if self._device.mode is not None:
            self._device.mode.unregister_device_updated_cb(self.after_update_callback)
            self._device.mode.xknx.devices.async_remove(self._device.mode)
        if self._status_text is not None:
            self._status_text.unregister_device_updated_cb(self.after_update_callback)
            self._status_text.xknx.devices.async_remove(self._status_text)"""),
    # YAML-Konstruktor
    ("""        fan_zero_mode: str = config[ClimateConf.FAN_ZERO_MODE]
        self._init_from_device_config(""",
     """        fan_zero_mode: str = config[ClimateConf.FAN_ZERO_MODE]
        if status_text_ga := config.get(ClimateSchema.CONF_STATUS_TEXT_STATE_ADDRESS):
            self._status_text = _create_status_text(
                knx_module.xknx,
                name=config[CONF_NAME],
                group_address_state=status_text_ga,
                value_type=config[ClimateSchema.CONF_STATUS_TEXT_TYPE],
            )
        self._init_from_device_config("""),
    # UI-Konstruktor -- get_state_and_passive() liefert [None] (truthy!), daher get_state
    ("""        fan_zero_mode = knx_conf.get(ClimateConf.FAN_ZERO_MODE)
        self._init_from_device_config(""",
     """        fan_zero_mode = knx_conf.get(ClimateConf.FAN_ZERO_MODE)
        if (status_text_ga := knx_conf.get_state(CONF_GA_STATUS_TEXT)) is not None:
            self._status_text = _create_status_text(
                knx_module.xknx,
                name=config[CONF_ENTITY][CONF_NAME],
                group_address_state=status_text_ga,
                value_type=(
                    "string"
                    if knx_conf.get_dpt(CONF_GA_STATUS_TEXT) == "16.000"
                    else "latin_1"
                ),
            )
        self._init_from_device_config("""),
])



# --- 5) Uebersetzungen -------------------------------------------------------
# strings.json ist die Quelle (fuer den PR), translations/*.json ist das, was das
# Frontend tatsaechlich ausliefert (im Container generiert vorhanden, im core-Klon
# nicht). Ohne Label humanisiert das KNX-Panel den Schluessel selbst.
LABEL = {
    "label": "Status text",
    "description": (
        "Diagnostic status text reported by the actuator (DPT 16.001), exposed as "
        "the `status_text` state attribute. Added by the KNX Interworking integration "
        "(remove that integration to revert this field)."
    ),
}

LABEL_DE = {
    "label": "Statustext",
    "description": (
        "Diagnose-Statustext des Aktors (DPT 16.001), verfügbar als Status-Attribut "
        "`status_text`. Hinzugefügt von der Integration „KNX Interworking" "
        "(zum Entfernen die Integration deinstallieren)."
    ),
}


def patch_json(path: Path, label: dict = LABEL) -> None:
    import collections
    import json as _json

    if not path.exists():
        print(f"  ~ {path.name}: nicht vorhanden (uebersprungen)")
        return
    d = _json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
    try:
        knx = d["config_panel"]["entities"]["create"]["climate"]["knx"]
    except KeyError:
        print(f"  ~ {path.name}: config_panel-Struktur fehlt (uebersprungen)")
        return
    if "ga_status_text" in knx:
        print(f"  ~ {path.name}: Label schon vorhanden")
        return
    knx["ga_status_text"] = label
    d["config_panel"]["entities"]["create"]["climate"]["knx"] = collections.OrderedDict(
        sorted(knx.items())
    )
    shutil.copy(path, path.with_suffix(path.suffix + SUFFIX))
    path.write_text(_json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  + {path.name} (Label ergaenzt)")


KNOWN_MODES = ("", "--check", "--revert", "--status", "--orphans")


def status() -> str:
    """Zustand des Patches in ROOT als ein Wort. rc immer 0.

    Verglichen wird gegen den Ersetzungstext, nicht gegen den Marker - eine
    veraltete Patch-Variante meldet dadurch `partial` statt falsch `applied`.
    Geprueft werden nur die Code-Dateien; strings.json/translations bleiben
    aussen vor, weil sie ohne gepatchte Quelle definitionsgemaess keine Luecke
    zeigen und den Status sonst zu optimistisch machen wuerden.
    """
    done = anchors_gone = total = 0
    for rel, (_marker, pairs) in EDITS.items():
        f = KNX / rel
        if not f.exists():
            return "file-missing"
        t = f.read_text(encoding="utf-8")
        total += 1
        if all(neu in t for _, neu in pairs):
            done += 1
        elif any(alt not in t for alt, _ in pairs):
            anchors_gone += 1
    if done == total:
        return "applied"
    if done:
        return "partial"
    return "anchors-missing" if anchors_gone else "missing"


def orphans() -> list[str]:
    """Backups, deren Datei der Patch nicht mehr anfasst."""
    known = set(EDITS) | {"strings.json", "translations/en.json", "translations/de.json"}
    return sorted(
        str(b.relative_to(KNX))[: -len(SUFFIX)]
        for b in KNX.rglob("*" + SUFFIX)
        if str(b.relative_to(KNX))[: -len(SUFFIX)] not in known
    )


def main() -> int:
    if MODE not in KNOWN_MODES:
        # Ohne diese Pruefung fiel JEDES unbekannte Flag in den Apply-Pfad.
        print(f"unbekannter Modus: {MODE}  (erlaubt: {' '.join(m or '<leer>=apply' for m in KNOWN_MODES)})")
        return 1

    if MODE == "--status":
        print(status())
        return 0

    if MODE == "--orphans":
        found = orphans()
        for rel in found:
            print(f"  WAISE: {rel}{SUFFIX}")
        if not found:
            print("keine verwaisten Backups")
        return 1 if found else 0

    if MODE == "--revert":
        for rel in list(EDITS) + ["strings.json", "translations/en.json", "translations/de.json"]:
            bak = KNX / (rel + SUFFIX)
            if bak.exists():
                shutil.copy(bak, KNX / rel)
                print(f"  zurueckgespielt: {rel}")
            else:
                print(f"  kein Backup: {rel}")
        return 0

    plan = []
    for rel, (marker, edits) in EDITS.items():
        f = KNX / rel
        if not f.exists():
            print(f"  FEHLT: {f}")
            return 2
        t = f.read_text(encoding="utf-8")
        if marker in t:
            print(f"  ~ {rel}: bereits gepatcht")
            continue
        missing = [a for a, _ in edits if a not in t]
        if missing:
            print(f"  ANKER FEHLT in {rel}: {len(missing)} von {len(edits)}")
            for a in missing:
                print(f"      {a.splitlines()[0][:88]}")
            return 2
        plan.append((f, rel, t, edits))
        print(f"  ok {rel}: {len(edits)} Anker gefunden")

    if MODE == "--check":
        print("--check: nichts geschrieben")
        return 0

    for f, rel, t, edits in plan:
        shutil.copy(f, KNX / (rel + SUFFIX))
        for alt, neu in edits:
            t = t.replace(alt, neu, 1)
        f.write_text(t, encoding="utf-8")
        print(f"  + {rel} (Backup: {rel}{SUFFIX})")
    patch_json(KNX / "strings.json")
    patch_json(KNX / "translations" / "en.json")
    patch_json(KNX / "translations" / "de.json", LABEL_DE)
    print("fertig — HA-Neustart noetig.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
