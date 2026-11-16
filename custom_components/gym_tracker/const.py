"""Constants for Gym Tracker."""

from __future__ import annotations

DOMAIN = "gym_tracker"
NAME = "Gym Tracker"
VERSION = "0.2.0"

DB_FILE = "gym_tracker.db"

PANEL_URL_PATH = "gym-tracker"
PANEL_COMPONENT = "gym-tracker-panel"
PANEL_ICON = "mdi:dumbbell"
STATIC_URL = f"/{DOMAIN}_static"
EXPORT_URL = f"/api/{DOMAIN}/export.csv"

CONF_SHOW_PANEL = "show_panel"
CONF_PANEL_TITLE = "panel_title"

SIGNAL_DATA_UPDATED = f"{DOMAIN}_data_updated"
