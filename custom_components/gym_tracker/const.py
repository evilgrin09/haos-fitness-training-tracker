"""Constants for Gym Tracker."""

from __future__ import annotations

DOMAIN = "gym_tracker"
NAME = "Gym Tracker"
VERSION = "0.3.1"

DB_FILE = "gym_tracker.db"

PANEL_URL_PATH = "gym-tracker"
PANEL_COMPONENT = "gym-tracker-panel"
PANEL_ICON = "mdi:dumbbell"
STATIC_URL = f"/{DOMAIN}_static"
EXPORT_URL = f"/api/{DOMAIN}/export.csv"

CONF_SHOW_PANEL = "show_panel"
CONF_PANEL_TITLE = "panel_title"

SIGNAL_DATA_UPDATED = f"{DOMAIN}_data_updated"
SIGNAL_DATA_UPDATED = f"{DOMAIN}_data_updated"

CONF_ALLOWED_USERS = "allowed_users"
CONF_LEGACY_OWNER = "legacy_owner"
# entry.data: user who received the data from before 0.3.0 (keeps the old sensor entity ids)
DATA_LEGACY_USER = "legacy_user"
# Owner of the data from before 0.3.0, matched against display name or user name
LEGACY_OWNER_NAME = "tim-mobile"
