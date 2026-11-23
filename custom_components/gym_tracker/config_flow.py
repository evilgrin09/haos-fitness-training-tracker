"""Config flow for Gym Tracker."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from .const import CONF_ALLOWED_USERS, CONF_LEGACY_OWNER, CONF_PANEL_TITLE, CONF_SHOW_PANEL, DOMAIN, NAME
from .store import GymStore, async_people


def _schema(defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(CONF_SHOW_PANEL, default=defaults.get(CONF_SHOW_PANEL, True)): bool,
        vol.Required(CONF_PANEL_TITLE, default=defaults.get(CONF_PANEL_TITLE, NAME)): str,
    }


def _user_select(users: list[tuple[str, str]], multiple: bool) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=user_id, label=name) for user_id, name in users],
            multiple=multiple,
            mode=selector.SelectSelectorMode.LIST if multiple else selector.SelectSelectorMode.DROPDOWN,
        )
    )


class GymTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=vol.Schema(_schema({})))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GymTrackerOptionsFlow()


class GymTrackerOptionsFlow(OptionsFlow):
    """Handle options: panel, allowed users and the owner of data from before 0.3.0."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = dict(self.config_entry.options)
        people = sorted(((user.id, user.name or user.id) for user in await async_people(self.hass)), key=lambda u: u[1])
        known = {user_id for user_id, _name in people}
        schema = _schema(options)
        schema[
            vol.Optional(
                CONF_ALLOWED_USERS,
                default=[user_id for user_id in options.get(CONF_ALLOWED_USERS, []) if user_id in known],
            )
        ] = _user_select(people, multiple=True)
        store: GymStore | None = (
            self.config_entry.runtime_data if self.config_entry.state is ConfigEntryState.LOADED else None
        )
        if store is not None and store.unassigned:
            # Only users without an own plan, so that two plans never have to be merged.
            candidates = [(user_id, name) for user_id, name in people if user_id not in store.summaries]
            schema[vol.Optional(CONF_LEGACY_OWNER)] = _user_select(candidates, multiple=False)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            description_placeholders={
                "plans": str(store.unassigned.get("plans", 0)) if store and store.unassigned else "0",
                "sessions": str(store.unassigned.get("sessions", 0)) if store and store.unassigned else "0",
            },
        )
