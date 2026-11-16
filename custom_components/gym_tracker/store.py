"""Async access to the training database for Home Assistant."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import DB_FILE, SIGNAL_DATA_UPDATED
from .db import GymDatabase


class GymStore:
    """Runs all database work in the executor and keeps the sensor figures cached."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self.hass = hass
        self.path = hass.config.path(DB_FILE)
        self.db: GymDatabase | None = None
        self.summary: dict[str, Any] = {}

    def _open(self) -> GymDatabase:
        db = GymDatabase(self.path)
        db.seed_default_plan(dt_util.utcnow())
        return db

    async def async_load(self) -> None:
        """Open the database and create the default plan on first start."""
        self.db = await self.hass.async_add_executor_job(self._open)
        await self.async_refresh(notify=False)

    async def async_close(self) -> None:
        """Close the database."""
        if self.db is not None:
            db, self.db = self.db, None
            await self.hass.async_add_executor_job(db.close)

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
        """Recalculate the cached figures (also needed when a new day starts)."""
        today = dt_util.now().date()
        self.summary = await self.async_read(lambda db: db.summary(today))
        if notify:
            async_dispatcher_send(self.hass, SIGNAL_DATA_UPDATED)
