"""WebSocket API used by the Gym Tracker panel."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from . import logic
from .const import DOMAIN
from .db import GymDatabase, GymError
from .store import GymStore

_RANGE = vol.In(list(logic.RANGE_DAYS))
_ID = vol.All(vol.Coerce(int), vol.Range(min=1))
_OPT_INT = vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0)))

Handler = Callable[[HomeAssistant, GymStore, dict[str, Any]], Awaitable[Any]]


def _handler(func: Handler) -> Callable[..., Awaitable[None]]:
    """Wrap a handler so that validation errors become websocket errors."""

    @wraps(func)
    async def wrapper(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
    ) -> None:
        store: GymStore | None = hass.data.get(DOMAIN)
        if store is None or store.db is None:
            connection.send_error(msg["id"], "not_loaded", "Gym Tracker is not set up")
            return
        try:
            result = await func(hass, store, msg)
        except GymError as err:
            connection.send_error(msg["id"], err.code, str(err))
            return
        connection.send_result(msg["id"], result)

    return wrapper


def _command(name: str, schema: dict[Any, Any] | None = None) -> Callable[[Handler], Any]:
    def decorator(func: Handler) -> Any:
        return websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/{name}", **(schema or {})})(
            websocket_api.async_response(_handler(func))
        )

    return decorator


@_command("overview")
async def ws_overview(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Everything the start screen needs."""
    today = dt_util.now().date()

    def job(db: GymDatabase) -> dict[str, Any]:
        running = db.running_session_id()
        return {
            "plans": db.get_plans(),
            "active_session": db.session_detail(running) if running else None,
            "recent_sessions": db.list_sessions(limit=5),
            "summary": db.summary(today),
            "today": today.isoformat(),
        }

    return await store.async_read(job)


@_command("session/start", {vol.Optional("plan_id"): _ID})
async def ws_session_start(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Start a session (or return the running one)."""
    now, today = dt_util.utcnow(), dt_util.now().date()
    return await store.async_write(lambda db: db.session_detail(db.start_session(msg.get("plan_id"), now, today)))


@_command("session/get", {vol.Required("session_id"): _ID})
async def ws_session_get(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Return a session with all entries."""
    return await store.async_read(lambda db: db.session_detail(msg["session_id"]))


@_command("session/warmup", {vol.Required("session_id"): _ID, vol.Required("done"): cv.boolean})
async def ws_session_warmup(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Tick off the warm-up."""

    def job(db: GymDatabase) -> dict[str, Any]:
        db.set_warmup_done(msg["session_id"], msg["done"])
        return db.session_detail(msg["session_id"])

    return await store.async_write(job)


@_command("session/update", {vol.Required("session_id"): _ID, vol.Required("note"): cv.string})
async def ws_session_update(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Change the note of a session."""

    def job(db: GymDatabase) -> dict[str, Any]:
        db.update_session(msg["session_id"], msg["note"].strip())
        return db.session_detail(msg["session_id"])

    return await store.async_write(job)


@_command("session/finish", {vol.Required("session_id"): _ID})
async def ws_session_finish(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Complete the running session."""
    now = dt_util.utcnow()

    def job(db: GymDatabase) -> dict[str, Any]:
        db.finish_session(msg["session_id"], now)
        return db.session_detail(msg["session_id"])

    return await store.async_write(job)


@_command("session/cancel", {vol.Required("session_id"): _ID})
async def ws_session_cancel(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Cancel the running session."""
    now = dt_util.utcnow()
    return {"kept": await store.async_write(lambda db: db.cancel_session(msg["session_id"], now))}


@_command("session/delete", {vol.Required("session_id"): _ID})
async def ws_session_delete(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> None:
    """Delete a session."""
    await store.async_write(lambda db: db.delete_session(msg["session_id"]))


@_command(
    "entry/save",
    {
        vol.Required("session_id"): _ID,
        vol.Required("exercise_id"): _ID,
        vol.Optional("skipped", default=False): cv.boolean,
        vol.Optional("sets"): _OPT_INT,
        vol.Optional("reps"): _OPT_INT,
        vol.Optional("seconds"): _OPT_INT,
        vol.Optional("weight"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0))),
        vol.Optional("rpe"): _OPT_INT,
        vol.Optional("note", default=""): cv.string,
        vol.Optional("uuid"): cv.string,
    },
)
async def ws_entry_save(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Record (or correct) an exercise of a session."""
    now = dt_util.utcnow()

    def job(db: GymDatabase) -> dict[str, Any]:
        db.save_entry(
            msg["session_id"],
            msg["exercise_id"],
            now,
            skipped=msg["skipped"],
            sets=msg.get("sets"),
            reps=msg.get("reps"),
            seconds=msg.get("seconds"),
            weight=msg.get("weight"),
            rpe=msg.get("rpe"),
            note=msg["note"].strip(),
            entry_uuid=msg.get("uuid"),
        )
        return db.session_detail(msg["session_id"])

    return await store.async_write(job)


@_command("entry/delete", {vol.Required("entry_id"): _ID})
async def ws_entry_delete(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Delete an entry."""
    return await store.async_write(lambda db: db.session_detail(db.delete_entry(msg["entry_id"])))


@_command(
    "sessions",
    {
        vol.Optional("limit", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
        vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    },
)
async def ws_sessions(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Past sessions, newest first."""
    return await store.async_read(lambda db: db.list_sessions(limit=msg["limit"], offset=msg["offset"]))


@_command("stats/overview", {vol.Optional("range", default="3m"): _RANGE})
async def ws_stats_overview(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """Overall figures of a range."""
    today = dt_util.now().date()
    since = logic.range_start(msg["range"], today)
    return await store.async_read(lambda db: db.overview_stats(since, today))


@_command("stats/exercise", {vol.Required("exercise_id"): _ID, vol.Optional("range", default="3m"): _RANGE})
async def ws_stats_exercise(hass: HomeAssistant, store: GymStore, msg: dict[str, Any]) -> dict[str, Any]:
    """History and records of an exercise."""
    since = logic.range_start(msg["range"], dt_util.now().date())
    return await store.async_read(lambda db: db.exercise_stats(msg["exercise_id"], since))


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register all websocket commands."""
    for command in (
        ws_overview,
        ws_session_start,
        ws_session_get,
        ws_session_warmup,
        ws_session_update,
        ws_session_finish,
        ws_session_cancel,
        ws_session_delete,
        ws_entry_save,
        ws_entry_delete,
        ws_sessions,
        ws_stats_overview,
        ws_stats_exercise,
    ):
        websocket_api.async_register_command(hass, command)


