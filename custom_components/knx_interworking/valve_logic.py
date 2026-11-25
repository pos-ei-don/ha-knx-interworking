"""Pure logic for the KNX valve platform — no Home Assistant import.

Kept separate so the mapping between Home Assistant valve positions and the
xknx ``Cover`` device can be unit tested without a Home Assistant test harness
(same idea as ``patches/_anchor.py``).

Why a ``Cover`` underneath: a motorised valve (mixing valve, zone valve on a
blind actuator channel) behaves like a blind — it has a travel time, a target
and an actual position, optionally a stop object. xknx ``Cover`` already models
all of that (``is_opening``/``is_closing``, travel calculator, stop). The valve
entity only changes the presentation: open/closed instead of up/down.

Position conventions:
- Home Assistant valve: 0 = closed, 100 = fully open.
- xknx ``Cover``: 0 = open (up), 100 = closed (down) — the KNX blind convention.
- ``invert`` is passed through as ``invert_position`` and has exactly the same
  meaning as the "Invert position" option of a KNX cover entity. A valve that
  replaces an existing KNX cover therefore keeps that cover's setting and
  sends the same bus values for the same Home Assistant position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xknx.telegram.address import GroupAddress

# Subentry data keys
CONF_NAME = "name"
CONF_GA_POSITION = "ga_position"
CONF_GA_POSITION_STATE = "ga_position_state"
CONF_GA_STOP = "ga_stop"
CONF_TRAVEL_TIME_DOWN = "travel_time_down"
CONF_TRAVEL_TIME_UP = "travel_time_up"
CONF_INVERT = "invert"
CONF_DEVICE_CLASS = "device_class"

DEVICE_CLASSES = ("none", "water", "gas")
DEFAULT_TRAVEL_TIME = 60.0
MAX_TRAVEL_TIME = 3600.0


class InvalidValveConfig(ValueError):
    """Raised with the name of the offending field (used as the flow error key)."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


def parse_ga(value: Any, field: str, *, required: bool) -> str | None:
    """Validate one group address. Returns it normalised (``1/4/168``) or None."""
    text = str(value or "").strip()
    if not text:
        if required:
            raise InvalidValveConfig(field, f"{field} is required")
        return None
    try:
        ga = GroupAddress(text)
    except Exception as err:  # xknx raises CouldNotParseAddress
        raise InvalidValveConfig(field, f"{field}: '{text}' is not a group address") from err
    if ga.raw == 0:
        # 0/0/0 is not a usable group address - almost always a typo.
        raise InvalidValveConfig(field, f"{field}: 0/0/0 is not usable")
    return str(ga)


def parse_travel_time(value: Any, field: str) -> float:
    """Travel time in seconds, > 0 and at most an hour."""
    try:
        seconds = float(value)
    except (TypeError, ValueError) as err:
        raise InvalidValveConfig(field, f"{field}: not a number") from err
    if not 0 < seconds <= MAX_TRAVEL_TIME:
        raise InvalidValveConfig(field, f"{field}: must be between 0 and {MAX_TRAVEL_TIME:.0f} s")
    return seconds


@dataclass(frozen=True)
class ValveConfig:
    """Validated configuration of one valve."""

    name: str
    ga_position: str
    ga_position_state: str | None
    ga_stop: str | None
    travel_time_down: float
    travel_time_up: float
    invert: bool
    device_class: str | None

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> ValveConfig:
        name = str(data.get(CONF_NAME) or "").strip()
        if not name:
            raise InvalidValveConfig(CONF_NAME, "name is required")
        device_class = data.get(CONF_DEVICE_CLASS) or "none"
        if device_class not in DEVICE_CLASSES:
            raise InvalidValveConfig(CONF_DEVICE_CLASS, f"unknown device class {device_class}")
        return cls(
            name=name,
            ga_position=parse_ga(data.get(CONF_GA_POSITION), CONF_GA_POSITION, required=True),
            ga_position_state=parse_ga(
                data.get(CONF_GA_POSITION_STATE), CONF_GA_POSITION_STATE, required=False
            ),
            ga_stop=parse_ga(data.get(CONF_GA_STOP), CONF_GA_STOP, required=False),
            travel_time_down=parse_travel_time(
                data.get(CONF_TRAVEL_TIME_DOWN, DEFAULT_TRAVEL_TIME), CONF_TRAVEL_TIME_DOWN
            ),
            travel_time_up=parse_travel_time(
                data.get(CONF_TRAVEL_TIME_UP, DEFAULT_TRAVEL_TIME), CONF_TRAVEL_TIME_UP
            ),
            invert=bool(data.get(CONF_INVERT, False)),
            device_class=None if device_class == "none" else device_class,
        )

    def as_data(self) -> dict[str, Any]:
        """Normalised data to store in the subentry."""
        return {
            CONF_NAME: self.name,
            CONF_GA_POSITION: self.ga_position,
            CONF_GA_POSITION_STATE: self.ga_position_state or "",
            CONF_GA_STOP: self.ga_stop or "",
            CONF_TRAVEL_TIME_DOWN: self.travel_time_down,
            CONF_TRAVEL_TIME_UP: self.travel_time_up,
            CONF_INVERT: self.invert,
            CONF_DEVICE_CLASS: self.device_class or "none",
        }

    def cover_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for ``xknx.devices.Cover`` (without ``xknx``/``name``).

        Without a status address the cover still works: xknx then derives the
        position from the travel time (same as a KNX cover without status).
        """
        return {
            "group_address_position": self.ga_position,
            "group_address_position_state": self.ga_position_state,
            "group_address_stop": self.ga_stop,
            "travel_time_down": self.travel_time_down,
            "travel_time_up": self.travel_time_up,
            "invert_position": self.invert,
            "sync_state": True,
        }

    @property
    def supports_stop(self) -> bool:
        return self.ga_stop is not None


def valve_position(cover_position: int | None) -> int | None:
    """xknx cover position (0 open .. 100 closed) -> valve position (0 closed .. 100 open)."""
    if cover_position is None:
        return None
    return 100 - int(cover_position)


def cover_position(valve_pos: int) -> int:
    """Valve position (0 closed .. 100 open) -> xknx cover position (0 open .. 100 closed)."""
    pos = max(0, min(100, int(valve_pos)))
    return 100 - pos
