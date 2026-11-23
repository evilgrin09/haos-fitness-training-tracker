"""Async access to the training database for Home Assistant."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any

from homeassistant.auth.models import User
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from . import logic
from .const import (
    CONF_ALLOWED_USERS,
    CONF_LEGACY_OWNER,
    DATA_LEGACY_USER,
    DB_FILE,
    LEGACY_OWNER_NAME,
    SIGNAL_DATA_UPDATED,
)
from .db import GymDatabase, GymError

_LOGGER = logging.getLogger(__name__)


def user_info(user: User) -> dict[str, Any]:
    """Display name and user names (of the Home Assistant login) of a user."""
    usernames = [cred.data["username"] for cred in user.credentials if cred.data.get("username")]
    return {"id": user.id, "name": user.name or "", "usernames": usernames}


async def async_people(hass: HomeAssistant) -> list[User]:
    """Active human users of Home Assistant (no system users)."""
    return [user for user in await hass.auth.async_get_users() if user.is_active and not user.system_generated]


class GymStore:
    """Runs all database work in the executor and keeps the sensor figures per user cached."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the store."""
        self.hass = hass
        self.entry = entry
        self.path = hass.config.path(DB_FILE)
        self.db: GymDatabase | None = None
        # user id -> figures for the sensors; contains every user with a plan
        self.summaries: dict[str, dict[str, Any]] = {}
        self.user_names: dict[str, str] = {}
        # plans/sessions from before 0.3.0 that could not be assigned yet
        self.unassigned: dict[str, int] = {}
        self.legacy_user: str | None = entry.data.get(DATA_LEGACY_USER)
        # Options this store was set up with; only a change of these needs a reload.
        self.options = dict(entry.options)

    async def async_load(self) -> None:
        """Open (and migrate) the database and assign data from before 0.3.0."""
        self.db = await self.hass.async_add_executor_job(GymDatabase, self.path)
        await self._async_assign_legacy_data()
        await self.async_refresh(notify=False)

    async def _async_assign_legacy_data(self) -> None:
        """Give unassigned data to the user chosen in the options, or else to "tim-mobile".

        Nothing is guessed: if that user cannot be found exactly once, the data stays
        unassigned (and untouched) until an admin picks the owner in the options.
        """
        counts = await self.async_read(lambda db: db.unassigned())
        if not any(counts.values()):
            self.unassigned = {}
            return
        people = [user_info(user) for user in await async_people(self.hass)]
        owner = self.entry.options.get(CONF_LEGACY_OWNER)
        if owner not in {person["id"] for person in people}:
            owner = logic.find_legacy_owner(people, LEGACY_OWNER_NAME)
        if owner is None:
            self.unassigned = counts
            _LOGGER.warning(
                "Gym Tracker: %s Plan/Pläne und %s Einheit(en) aus der Zeit vor 0.3.0 sind keinem Benutzer "
                "zugeordnet, weil der Benutzer „%s“ nicht eindeutig gefunden wurde. Die Daten bleiben erhalten; "
                "ein Admin kann sie in den Optionen der Integration zuordnen",
                counts["plans"],
                counts["sessions"],
                LEGACY_OWNER_NAME,
            )
            return
        try:
            await self.async_read(lambda db: db.assign_unassigned(owner))
        except GymError as err:
            self.unassigned = counts
            _LOGGER.error("Gym Tracker: Bestehende Daten konnten nicht zugeordnet werden: %s", err)
            return
        self.unassigned = {}
        self.legacy_user = owner
        self.hass.config_entries.async_update_entry(self.entry, data={**self.entry.data, DATA_LEGACY_USER: owner})
        _LOGGER.info(
            "Gym Tracker: %s Plan/Pläne und %s Einheit(en) dem Benutzer %s zugeordnet",
            counts["plans"],
            counts["sessions"],
            owner,
        )

    async def async_retry_assignment(self) -> None:
        """Try again to assign data from before 0.3.0, e.g. after "tim-mobile" logged in for the first time.

        Home Assistant creates a user only on the first login, so the owner may not have existed
        when the integration started.
        """
        if not self.unassigned:
            return
        await self._async_assign_legacy_data()
        if not self.unassigned:
            await self.async_refresh()

    async def async_close(self) -> None:
        """Close the database."""
        if self.db is not None:
            db, self.db = self.db, None
            await self.hass.async_add_executor_job(db.close)

    def allowed(self, user_id: str) -> bool:
        """Whether a user may use the panel (empty list in the options = everybody)."""
        allowed = self.entry.options.get(CONF_ALLOWED_USERS) or []
        return not allowed or user_id in allowed

    async def async_read[R](self, func: Callable[[GymDatabase], R]) -> R:
        """Run a read-only database job."""
        assert self.db is not None
        return await self.hass.async_add_executor_job(func, self.db)

    async def async_write[R](self, func: Callable[[GymDatabase], R]) -> R:
        """Run a database job that changes data and tell the listeners."""
        result = await self.async_read(func)
        await self.async_refresh()
        return result

    async def async_refresh(self, notify: bool = True) -> None:
        """Recalculate the cached figures of all users (also needed when a new day starts)."""
        today = dt_util.now().date()

        def job(db: GymDatabase) -> dict[str, dict[str, Any]]:
            return {user_id: db.summary(user_id, today) for user_id in db.plan_users()}

        self.summaries = await self.async_read(job)
        self.user_names = {user.id: user.name or user.id for user in await self.hass.auth.async_get_users()}
        if notify:
            async_dispatcher_send(self.hass, SIGNAL_DATA_UPDATED)
