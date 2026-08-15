"""Map a YAML KNX climate entry to the UI entity-store shape.

The two schemas name the same things differently: YAML has flat
``*_address`` / ``*_state_address`` pairs, the store has one ``ga_*`` object per
function with ``write`` / ``state`` / ``passive`` inside.

⭐ **Nothing is dropped silently.** Only the mapping below is applied; every
remaining YAML key is either carried over because the store schema happens to
accept that exact name, or **reported as unmapped**. A converter that quietly
loses ``min_temp`` would be worse than no converter at all — the entity would
look fine and behave differently.

⚠️ The conversion changes the entity_id. A YAML entity derives its unique_id
from its group addresses, a store entity gets a fresh one, so the new entity
arrives as ``climate.<name>_2`` while dashboards, automations and the recorded
history still point at the old one. That is the expensive part of the move, not
the mapping — which is why the report states it per entity.
"""

from __future__ import annotations

from typing import Any

# UI key -> (YAML key for write, YAML key for state)
GA_MAP: dict[str, tuple[str | None, str | None]] = {
    "ga_temperature_current": (None, "temperature_address"),
    "ga_humidity_current": (None, "humidity_state_address"),
    "ga_status_text": (None, "status_text_state_address"),
    "ga_active": (None, "active_state_address"),
    "ga_valve": (None, "command_value_state_address"),
    "ga_operation_mode": ("operation_mode_address", "operation_mode_state_address"),
    "ga_operation_mode_comfort": ("operation_mode_comfort_address", None),
    "ga_operation_mode_economy": ("operation_mode_night_address", None),
    "ga_operation_mode_standby": ("operation_mode_standby_address", None),
    "ga_operation_mode_protection": ("operation_mode_frost_protection_address", None),
    "ga_heat_cool": ("heat_cool_address", "heat_cool_state_address"),
    "ga_on_off": ("on_off_address", "on_off_state_address"),
    "ga_controller_mode": ("controller_mode_address", "controller_mode_state_address"),
    "ga_controller_status": ("controller_status_address", "controller_status_state_address"),
    "ga_fan_speed": ("fan_speed_address", "fan_speed_state_address"),
    "ga_fan_swing": ("swing_address", "swing_state_address"),
    "ga_fan_swing_horizontal": ("swing_horizontal_address", "swing_horizontal_state_address"),
}

# Handled outside the knx block of the store entry.
ENTITY_KEYS = {"name", "entity_category"}

# The store groups the target temperature into one nested block with two mutually
# exclusive shapes (verified against a real stored entity, not guessed):
#   direct        -> ga_temperature_target (write) + min_temp/max_temp/temperature_step
#   setpoint shift-> ga_temperature_target (state only) + ga_setpoint_shift
#                    + setpoint_shift_min/max + temperature_step
TARGET_KEYS = {
    "target_temperature_address", "target_temperature_state_address",
    "setpoint_shift_address", "setpoint_shift_state_address", "setpoint_shift_mode",
    "min_temp", "max_temp", "setpoint_shift_min", "setpoint_shift_max",
    "temperature_step",
}

# YAML names the mode after the DPT class, the store stores the DPT itself.
SHIFT_DPT = {"DPT6010": "6.010", "DPT9002": "9.002"}


def _ga(write: Any, state: Any) -> dict[str, Any] | None:
    """Build one ga_* object. YAML allows a list — first is active, rest passive."""

    def split(value: Any) -> tuple[Any, list[Any]]:
        if value is None:
            return None, []
        if isinstance(value, list):
            return (value[0], list(value[1:])) if value else (None, [])
        return value, []

    w, w_passive = split(write)
    s, s_passive = split(state)
    if w is None and s is None:
        return None
    passive = [str(a) for a in (*w_passive, *s_passive)]
    # Omit what is absent instead of sending null - that is how the store
    # writes it, and the schema rejects unexpected keys.
    result: dict[str, Any] = {}
    if w:
        result["write"] = str(w)
    if s:
        result["state"] = str(s)
    result["passive"] = passive
    return result


def convert_climate(entry: dict[str, Any], ui_keys: set[str]) -> dict[str, Any]:
    """Return {'data': <store entry>, 'unmapped': {...}, 'notes': [...]}.

    ``ui_keys`` are the key names the store schema accepts for this platform —
    passed in rather than imported here so the caller owns the (internal) import
    and this module stays testable on its own.
    """
    used: set[str] = set()
    knx: dict[str, Any] = {}
    notes: list[str] = []

    # --- the target temperature block ------------------------------------
    target: dict[str, Any] = {}
    shift_w = entry.get("setpoint_shift_address")
    shift_s = entry.get("setpoint_shift_state_address")
    if shift_w or shift_s:
        target["ga_temperature_target"] = _ga(None, entry.get("target_temperature_state_address")
                                              or entry.get("target_temperature_address")) or {"passive": []}
        shift = _ga(shift_w, shift_s) or {"passive": []}
        mode = entry.get("setpoint_shift_mode")
        if mode:
            shift["dpt"] = SHIFT_DPT.get(str(mode), str(mode))
        target["ga_setpoint_shift"] = shift
        for src, dst, default in (("setpoint_shift_min", "setpoint_shift_min", -6),
                                  ("setpoint_shift_max", "setpoint_shift_max", 6),
                                  ("temperature_step", "temperature_step", 0.1)):
            target[dst] = entry.get(src, default)
    else:
        target["ga_temperature_target"] = _ga(entry.get("target_temperature_address"),
                                              entry.get("target_temperature_state_address")) or {"passive": []}
        for src, dst, default in (("min_temp", "min_temp", 7),
                                  ("max_temp", "max_temp", 28),
                                  ("temperature_step", "temperature_step", 0.1)):
            target[dst] = entry.get(src, default)
    knx["target_temperature"] = target
    used |= {k for k in TARGET_KEYS if k in entry}

    for ui_key, (yaml_write, yaml_state) in GA_MAP.items():
        write = entry.get(yaml_write) if yaml_write else None
        state = entry.get(yaml_state) if yaml_state else None
        if yaml_write and yaml_write in entry:
            used.add(yaml_write)
        if yaml_state and yaml_state in entry:
            used.add(yaml_state)
        block = _ga(write, state)
        if block is not None:
            knx[ui_key] = block

    # Anything left that the store happens to accept under the same name.
    for key, value in entry.items():
        if key in used or key in ENTITY_KEYS:
            continue
        if key in ui_keys:
            knx[key] = value
            used.add(key)
            notes.append(f"carried over unchanged: {key}")

    unmapped = {k: v for k, v in entry.items() if k not in used and k not in ENTITY_KEYS}

    data = {
        "entity": {
            "name": entry.get("name"),
            "device_info": None,
            "entity_category": entry.get("entity_category"),
        },
        "knx": knx,
    }
    return {"data": data, "unmapped": unmapped, "notes": notes}
