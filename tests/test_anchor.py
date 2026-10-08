"""Unit tests for the tolerant anchor matching of the core file patches.

No Home Assistant needed: ``patches/_anchor.py`` is plain Python.

    python -m pytest tests/test_anchor.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "custom_components" / "knx_interworking" / "patches")
)

import _anchor  # noqa: E402

SCHEMA_VOL = '''import voluptuous as vol

SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_A): ga_list_validator,
        vol.Optional(CONF_B): ga_list_validator,
        vol.Optional(CONF_C): ga_list_validator,
        vol.Optional(CONF_D): ga_list_validator,
    }
)
'''

SCHEMA_PROBATIO = '''import probatio

SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_A): ga_list_validator,
        probatio.Optional(
            CONF_B
        ): ga_list_validator,  # reformatted by a newer ruff
        probatio.Optional(CONF_C): ga_list_validator,
        probatio.Optional(CONF_D): ga_list_validator,
    }
)
'''

HUNK = [
    "        vol.Optional(CONF_B): ga_list_validator,\n",
    "        vol.Optional(CONF_B): ga_list_validator,\n"
    "        vol.Optional(CONF_NEW, default=False): cv.boolean,\n",
]


def test_exact_anchor_still_applies() -> None:
    out, problems = _anchor.apply_hunks(SCHEMA_VOL, [HUNK])
    assert problems == []
    assert "vol.Optional(CONF_NEW, default=False): cv.boolean," in out
    compile(out, "x", "exec")


def test_alias_and_reformatting_are_tolerated() -> None:
    """vol -> probatio and a re-wrapped line must not break the anchor."""
    out, problems = _anchor.apply_hunks(SCHEMA_PROBATIO, [HUNK])
    assert problems == []
    # written with the alias the target file imports
    assert "probatio.Optional(CONF_NEW, default=False): cv.boolean," in out
    assert "vol." not in out
    compile(out, "x", "exec")
    # and recognised as applied afterwards
    assert _anchor.hunk_state(out, HUNK)[0] == _anchor.STATE_APPLIED


def test_apply_is_idempotent() -> None:
    once, _ = _anchor.apply_hunks(SCHEMA_PROBATIO, [HUNK])
    twice, problems = _anchor.apply_hunks(once, [HUNK])
    assert problems == []
    assert twice == once


def test_ambiguous_place_writes_nothing() -> None:
    text = "x = [\n    a,\n    b,\n]\ny = [\n    a,\n    b,\n]\n"
    hunk = ["    a,\n    b,\n", "    a,\n    NEW,\n    b,\n"]
    out, problems = _anchor.apply_hunks(text, [hunk])
    assert out == text
    assert problems and "ambiguous" in problems[0]


def test_missing_anchor_writes_nothing() -> None:
    text = "def f():\n    return 1\n"
    out, problems = _anchor.apply_hunks(text, [HUNK])
    assert out == text
    assert problems


def test_requires_selects_the_matching_version() -> None:
    hunk = [
        {
            "requires": ["config.entity.xknx_name"],
            "old": "        fan = knx_conf.get(X)\n        self.init(",
            "new": "        fan = knx_conf.get(X)\n        name = config.entity.xknx_name\n        self.init(",
        },
        {
            "requires": ["config[CONF_ENTITY][CONF_NAME]"],
            "old": "        fan = knx_conf.get(X)\n        self.init(",
            "new": "        fan = knx_conf.get(X)\n        name = config[CONF_ENTITY][CONF_NAME]\n        self.init(",
        },
    ]
    new_core = (
        "class E:\n    def __init__(self, config):\n"
        "        self.n = config.entity.xknx_name\n"
        "        fan = knx_conf.get(X)\n        self.init(fan)\n"
    )
    old_core = new_core.replace("config.entity.xknx_name", "config[CONF_ENTITY][CONF_NAME]")
    out_new, _ = _anchor.apply_hunks(new_core, [hunk])
    out_old, _ = _anchor.apply_hunks(old_core, [hunk])
    assert "name = config.entity.xknx_name" in out_new
    assert "name = config[CONF_ENTITY][CONF_NAME]" in out_old
    # neither version fits a file that has neither marker
    bare = new_core.replace("config.entity.xknx_name", "None")
    _out, problems = _anchor.apply_hunks(bare, [hunk])
    assert problems and "no variant fits" in problems[0]


def test_safety_check_catches_a_name_the_file_no_longer_imports() -> None:
    """The 2026.10 case: CONF_ENTITY vanished from the imports of cover.py."""
    before = (
        "from .const import KNX_MODULE_KEY\n\n"
        "class C:\n    def __init__(self, config):\n        self.x = config.knx\n"
    )
    after = before.replace(
        "self.x = config.knx\n",
        "self.x = config.knx\n        self.n = config[CONF_ENTITY][CONF_NAME]\n",
    )
    problems = _anchor.check_source("cover.py", before, after)
    assert problems and "CONF_ENTITY" in problems[0] and "CONF_NAME" in problems[0]


def test_safety_check_ignores_names_that_were_already_unresolved() -> None:
    before = "from x import *\n\nvalue = SOMETHING\n"
    after = before + "other = SOMETHING\n"
    assert _anchor.check_source("m.py", before, after) == []


def test_safety_check_reports_syntax_errors() -> None:
    problems = _anchor.check_source("m.py", "a = 1\n", "a = (1\n")
    assert problems and "does not compile" in problems[0]
