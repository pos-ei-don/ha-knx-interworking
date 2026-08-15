"""Config and options flow.

The options dialog is the heart of the user experience: every feature is a
single switch, grouped so that the harmless things (diagnostics) are visually
separate from the ones that change behaviour.

The dialog does **not** know any feature's fields. Each feature declares them
via ``Feature.options_schema()`` and this module only groups and stores them —
otherwise every new feature would mean editing the flow, and a forgotten line
here would silently drop a user's setting.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import CONF_FEATURES, CONF_SAFE_MODE, DOMAIN, Category
from .features.catalog import FEATURE_CLASSES

SECTION_DIAGNOSTICS = "diagnostics"
SECTION_INTERWORKING = "interworking"
SECTION_SAFETY = "safety"


def _section_for(category: Category) -> str:
    """Which group a feature belongs into."""
    return (
        SECTION_DIAGNOSTICS
        if category is Category.DIAGNOSTICS
        else SECTION_INTERWORKING
    )


class KnxInterworkingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the single instance of this integration."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """No configuration needed to attach — everything is in the options."""
        if user_input is not None:
            return self.async_create_entry(title="KNX Interworking", data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    def async_get_options_flow(config_entry: Any) -> OptionsFlow:
        """Return the options flow."""
        return KnxInterworkingOptionsFlow()


class KnxInterworkingOptionsFlow(OptionsFlow):
    """One switch per feature plus that feature's own fields, grouped by category."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the feature selection."""
        options = self.config_entry.options
        selected: dict[str, Any] = options.get(CONF_FEATURES, {})

        if user_input is not None:
            features: dict[str, bool] = {}
            new_options: dict[str, Any] = {
                CONF_SAFE_MODE: bool(
                    user_input.get(SECTION_SAFETY, {}).get(CONF_SAFE_MODE, False)
                ),
            }
            for cls in FEATURE_CLASSES:
                bucket = user_input.get(_section_for(cls.category), {})
                features[cls.key] = bool(bucket.get(cls.key, False))
                # Carry over the feature's own fields, whatever they are.
                for key in cls.option_keys():
                    if key in bucket:
                        new_options[key] = bucket[key]
            new_options[CONF_FEATURES] = features
            return self.async_create_entry(data=new_options)

        def fields_for(category: Category) -> dict[Any, Any]:
            fields: dict[Any, Any] = {}
            seen: set[str] = set()
            for cls in FEATURE_CLASSES:
                if cls.category is not category:
                    continue
                fields[
                    vol.Optional(
                        cls.key, default=selected.get(cls.key, cls.default_enabled)
                    )
                ] = selector.BooleanSelector()
                # Features may share an option (all file patches share
                # patch_auto_restore). Show it once, not once per feature.
                for marker, sel in cls.options_schema(options).items():
                    if marker.schema in seen:
                        continue
                    seen.add(marker.schema)
                    fields[marker] = sel
            return fields

        schema_fields: dict[Any, Any] = {}
        # Only show a group that actually has content - an empty section looks broken.
        for name, category, collapsed in (
            (SECTION_DIAGNOSTICS, Category.DIAGNOSTICS, False),
            (SECTION_INTERWORKING, Category.INTERWORKING, False),
        ):
            group = fields_for(category)
            if group:
                schema_fields[vol.Required(name)] = section(
                    vol.Schema(group), {"collapsed": collapsed}
                )

        schema_fields[vol.Required(SECTION_SAFETY)] = section(
            vol.Schema(
                {
                    vol.Optional(
                        CONF_SAFE_MODE, default=options.get(CONF_SAFE_MODE, False)
                    ): selector.BooleanSelector()
                }
            ),
            {"collapsed": True},
        )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema_fields))
