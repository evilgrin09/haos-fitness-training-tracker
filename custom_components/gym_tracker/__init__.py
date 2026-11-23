"""Gym Tracker: guided strength training with history and charts."""

from __future__ import annotations

from datetime import datetime
import hashlib
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import (
    CONF_PANEL_TITLE,
    CONF_SHOW_PANEL,
    DOMAIN,
    NAME,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_URL_PATH,
    STATIC_URL,
)
from .export import ExportView
from .store import GymStore

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

FRONTEND_DIR = Path(__file__).parent / "frontend"
PANEL_FILE = "gym-tracker-panel.js"

type GymTrackerConfigEntry = ConfigEntry[GymStore]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the parts that live independent of the config entry."""
    websocket.async_register(hass)
    hass.http.register_view(ExportView())
    await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), False)])
    return True


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


async def _async_register_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    version = await hass.async_add_executor_job(_file_hash, FRONTEND_DIR / PANEL_FILE)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_COMPONENT,
        frontend_url_path=PANEL_URL_PATH,
        module_url=f"{STATIC_URL}/{PANEL_FILE}?v={version}",
        sidebar_title=entry.options.get(CONF_PANEL_TITLE, NAME),
        sidebar_icon=PANEL_ICON,
        require_admin=False,
        config={},
    )


async def async_setup_entry(hass: HomeAssistant, entry: GymTrackerConfigEntry) -> bool:
    """Set up Gym Tracker from a config entry."""
    store = GymStore(hass, entry)
    await store.async_load()
    hass.data[DOMAIN] = store
    entry.runtime_data = store

    if entry.options.get(CONF_SHOW_PANEL, True):
        await _async_register_panel(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _new_day(_now: datetime) -> None:
        """Week and month counters change at midnight."""
        await store.async_refresh()

    entry.async_on_unload(async_track_time_change(hass, _new_day, hour=0, minute=0, second=10))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: GymTrackerConfigEntry) -> None:
    """Reload when the options change (not when the store records the owner of old data in entry.data)."""
    if dict(entry.options) != entry.runtime_data.options:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GymTrackerConfigEntry) -> bool:
    """Unload a config entry. The database file is kept, also when the integration is removed."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
        hass.data.pop(DOMAIN, None)
        await entry.runtime_data.async_close()
    return unloaded
