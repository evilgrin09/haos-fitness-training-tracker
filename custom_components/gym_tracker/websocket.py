"""WebSocket API used by the Gym Tracker panel.

Every command works on the data of the Home Assistant user of the connection only;
ids of other users' data are answered with "not_found".
"""

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
from .db import KINDS, GymDatabase, GymError
from .store import GymStore

_RANGE = vol.In(list(logic.RANGE_DAYS))
_ID = vol.All(vol.Coerce(int), vol.Range(min=1))
_OPT_INT = vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=0)))

_DIRECTION = vol.All(vol.In(["up", "down"]), lambda value: -1 if value == "up" else 1)
_OPT_STR = vol.Any(None, cv.string)

# "type" is reserved by the websocket protocol, so the kind of an exercise is sent as "kind".
EXERCISE_SCHEMA = {
    vol.Required("name"): cv.string,
    vol.Required("kind"): vol.In(KINDS),
    vol.Required("target_sets"): vol.Coerce(int),
    vol.Optional("reps_min"): vol.Any(None, vol.Coerce(int)),
    vol.Optional("reps_max"): vol.Any(None, vol.Coerce(int)),
    vol.Optional("duration_s"): vol.Any(None, vol.Coerce(int)),
    vol.Optional("start_weight"): vol.Any(None, vol.Coerce(float)),
    vol.Optional("muscle_group"): _OPT_STR,
    vol.Optional("setting"): _OPT_STR,
    vol.Optional("hint"): _OPT_STR,
}
EXERCISE_FIELDS = tuple(str(key) for key in EXERCISE_SCHEMA)

Handler = Callable[[HomeAssistant, GymStore, dict[str, Any], str], Awaitable[Any]]


def _handler(func: Handler) -> Callable[..., Awaitable[None]]:
    """Wrap a handler: check access, pass the user id, turn validation errors into websocket errors."""

    @wraps(func)
    async def wrapper(
        hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
    ) -> None:
        store: GymStore | None = hass.data.get(DOMAIN)
        if store is None or store.db is None:
            connection.send_error(msg["id"], "not_loaded", "Gym Tracker is not set up")
            return
        user = connection.user
        if user is None or not store.allowed(user.id):
            connection.send_error(msg["id"], "no_access", "This user may not use Gym Tracker")
            return
        try:
            result = await func(hass, store, msg, user.id)
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
async def ws_overview(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Everything the start screen needs."""
    today = dt_util.now().date()
    await store.async_retry_assignment()

    def job(db: GymDatabase) -> dict[str, Any]:
        running = db.running_session_id(uid)
        return {
            "plans": db.get_plans(uid),
            "active_session": db.session_detail(uid, running) if running else None,
            "recent_sessions": db.list_sessions(uid, limit=5),
            "summary": db.summary(uid, today),
            "today": today.isoformat(),
        }

    result = await store.async_read(job)
    user = await hass.auth.async_get_user(uid)
    result["user"] = {"id": uid, "name": user.name if user else "", "is_admin": bool(user and user.is_admin)}
    # Data from before 0.3.0 that belongs to nobody yet: everybody gets a hint, admins the numbers.
    result["unassigned"] = (
        dict(store.unassigned) if store.unassigned and user and user.is_admin else bool(store.unassigned)
    )
    return result


@_command(
    "plan/create",
    {
        vol.Optional("template", default=False): cv.boolean,
        vol.Optional("name"): _OPT_STR,
        vol.Optional("freq_min"): vol.Any(None, vol.Coerce(int)),
        vol.Optional("freq_max"): vol.Any(None, vol.Coerce(int)),
    },
)
async def ws_plan_create(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> list[dict[str, Any]]:
    """Create the plan of the user: a copy of the template or an empty plan."""
    now = dt_util.utcnow()

    def job(db: GymDatabase) -> list[dict[str, Any]]:
        db.create_user_plan(
            uid,
            now,
            template=msg["template"],
            name=msg.get("name"),
            freq_min=msg.get("freq_min"),
            freq_max=msg.get("freq_max"),
        )
        return db.get_plans(uid)

    return await store.async_write(job)


@_command("session/start", {vol.Optional("plan_id"): _ID})
async def ws_session_start(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Start a session (or return the running one)."""
    now, today = dt_util.utcnow(), dt_util.now().date()
    return await store.async_write(
        lambda db: db.session_detail(uid, db.start_session(uid, msg.get("plan_id"), now, today))
    )


@_command("session/get", {vol.Required("session_id"): _ID})
async def ws_session_get(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Return a session with all entries."""
    return await store.async_read(lambda db: db.session_detail(uid, msg["session_id"]))


@_command("session/warmup", {vol.Required("session_id"): _ID, vol.Required("done"): cv.boolean})
async def ws_session_warmup(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Tick off the warm-up."""

    def job(db: GymDatabase) -> dict[str, Any]:
        db.set_warmup_done(uid, msg["session_id"], msg["done"])
        return db.session_detail(uid, msg["session_id"])

    return await store.async_write(job)


@_command("session/update", {vol.Required("session_id"): _ID, vol.Required("note"): cv.string})
async def ws_session_update(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Change the note of a session."""

    def job(db: GymDatabase) -> dict[str, Any]:
        db.update_session(uid, msg["session_id"], msg["note"].strip())
        return db.session_detail(uid, msg["session_id"])

    return await store.async_write(job)


@_command("session/finish", {vol.Required("session_id"): _ID, vol.Optional("adopt", default=list): [_ID]})
async def ws_session_finish(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Complete the running session."""
    now = dt_util.utcnow()

    def job(db: GymDatabase) -> dict[str, Any]:
        db.finish_session(uid, msg["session_id"], now, msg["adopt"])
        return db.session_detail(uid, msg["session_id"])

    return await store.async_write(job)


@_command("session/cancel", {vol.Required("session_id"): _ID})
async def ws_session_cancel(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Cancel the running session."""
    now = dt_util.utcnow()
    return {"kept": await store.async_write(lambda db: db.cancel_session(uid, msg["session_id"], now))}


@_command("session/delete", {vol.Required("session_id"): _ID})
async def ws_session_delete(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> None:
    """Delete a session."""
    await store.async_write(lambda db: db.delete_session(uid, msg["session_id"]))


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
async def ws_entry_save(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Record (or correct) an exercise of a session."""
    now = dt_util.utcnow()

    def job(db: GymDatabase) -> dict[str, Any]:
        db.save_entry(
            uid,
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
        return db.session_detail(uid, msg["session_id"])

    return await store.async_write(job)


@_command("entry/delete", {vol.Required("entry_id"): _ID})
async def ws_entry_delete(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Delete an entry."""
    return await store.async_write(lambda db: db.session_detail(uid, db.delete_entry(uid, msg["entry_id"])))


@_command(
    "sessions",
    {
        vol.Optional("limit", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=500)),
        vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    },
)
async def ws_sessions(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> list[dict[str, Any]]:
    """Past sessions, newest first."""
    return await store.async_read(lambda db: db.list_sessions(uid, limit=msg["limit"], offset=msg["offset"]))


@_command("stats/overview", {vol.Optional("range", default="3m"): _RANGE})
async def ws_stats_overview(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Overall figures of a range."""
    today = dt_util.now().date()
    since = logic.range_start(msg["range"], today)
    return await store.async_read(lambda db: db.overview_stats(uid, since, today))


@_command("stats/exercise", {vol.Required("exercise_id"): _ID, vol.Optional("range", default="3m"): _RANGE})
async def ws_stats_exercise(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """History and records of an exercise."""
    since = logic.range_start(msg["range"], dt_util.now().date())
    return await store.async_read(lambda db: db.exercise_stats(uid, msg["exercise_id"], since))


_DEVICE = vol.In(list(logic.CARDIO_DEVICES))
_MINUTES = vol.All(vol.Coerce(int), vol.Range(min=logic.CARDIO_MIN_MINUTES, max=logic.CARDIO_MAX_MINUTES))


@_command(
    "cardio/add",
    {
        vol.Required("session_id"): _ID,
        vol.Required("phase"): vol.In(list(logic.CARDIO_PHASES)),
        vol.Required("device"): _DEVICE,
        vol.Required("minutes"): _MINUTES,
        vol.Optional("uuid"): cv.string,
    },
)
async def ws_cardio_add(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Add a cardio entry to the warm-up or cool-down of a session."""
    now = dt_util.utcnow()

    def job(db: GymDatabase) -> dict[str, Any]:
        db.add_cardio(uid, msg["session_id"], msg["phase"], msg["device"], msg["minutes"], now, msg.get("uuid"))
        return db.session_detail(uid, msg["session_id"])

    return await store.async_write(job)


@_command(
    "cardio/update",
    {vol.Required("cardio_id"): _ID, vol.Required("device"): _DEVICE, vol.Required("minutes"): _MINUTES},
)
async def ws_cardio_update(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Change a cardio entry."""
    return await store.async_write(
        lambda db: db.session_detail(uid, db.update_cardio(uid, msg["cardio_id"], msg["device"], msg["minutes"]))
    )


@_command("cardio/delete", {vol.Required("cardio_id"): _ID})
async def ws_cardio_delete(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> dict[str, Any]:
    """Delete a cardio entry."""
    return await store.async_write(lambda db: db.session_detail(uid, db.delete_cardio(uid, msg["cardio_id"])))


def _exercise_data(msg: dict[str, Any]) -> dict[str, Any]:
    return {key: msg.get(key) for key in EXERCISE_FIELDS}


def _plan_command(
    name: str, schema: dict[Any, Any], change: Callable[[GymDatabase, dict[str, Any], str], Any]
) -> Any:
    """Command that changes the plan and returns the user's plans afterwards."""

    async def handler(hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str) -> list[dict[str, Any]]:
        def job(db: GymDatabase) -> list[dict[str, Any]]:
            change(db, msg, uid)
            return db.get_plans(uid)

        return await store.async_write(job)

    return _command(name, schema)(handler)


ws_exercise_add = _plan_command(
    "exercise/add",
    {vol.Optional("plan_id"): _ID, **EXERCISE_SCHEMA},
    lambda db, msg, uid: db.add_exercise(uid, msg.get("plan_id"), _exercise_data(msg)),
)
ws_exercise_update = _plan_command(
    "exercise/update",
    {vol.Required("exercise_id"): _ID, **EXERCISE_SCHEMA},
    lambda db, msg, uid: db.update_exercise(uid, msg["exercise_id"], _exercise_data(msg)),
)
ws_exercise_move = _plan_command(
    "exercise/move",
    {vol.Required("exercise_id"): _ID, vol.Required("direction"): _DIRECTION},
    lambda db, msg, uid: db.move_exercise(uid, msg["exercise_id"], msg["direction"]),
)
ws_group_move = _plan_command(
    "exercise/move_group",
    {vol.Optional("plan_id"): _ID, vol.Required("group"): cv.string, vol.Required("direction"): _DIRECTION},
    lambda db, msg, uid: db.move_group(uid, msg.get("plan_id"), msg["group"], msg["direction"]),
)
ws_exercise_archive = _plan_command(
    "exercise/archive",
    {vol.Required("exercise_id"): _ID},
    lambda db, msg, uid: db.archive_exercise(uid, msg["exercise_id"]),
)
ws_exercise_restore = _plan_command(
    "exercise/restore",
    {vol.Required("exercise_id"): _ID},
    lambda db, msg, uid: db.restore_exercise(uid, msg["exercise_id"]),
)


@_command(
    "session/add_exercise",
    {
        vol.Required("session_id"): _ID,
        vol.Optional("exercise_id"): _ID,
        **{vol.Optional(str(key)): value for key, value in EXERCISE_SCHEMA.items()},
    },
)
async def ws_session_add_exercise(
    hass: HomeAssistant, store: GymStore, msg: dict[str, Any], uid: str
) -> dict[str, Any]:
    """Add a spontaneous exercise (new, or an archived one) to the running session."""

    def job(db: GymDatabase) -> dict[str, Any]:
        exercise_id = db.add_session_exercise(
            uid, msg["session_id"], None if "exercise_id" in msg else _exercise_data(msg), msg.get("exercise_id")
        )
        return {"exercise_id": exercise_id, "session": db.session_detail(uid, msg["session_id"])}

    return await store.async_write(job)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register all websocket commands."""
    for command in (
        ws_overview,
        ws_plan_create,
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
        ws_exercise_add,
        ws_exercise_update,
        ws_exercise_move,
        ws_group_move,
        ws_exercise_archive,
        ws_exercise_restore,
        ws_session_add_exercise,
        ws_cardio_add,
        ws_cardio_update,
        ws_cardio_delete,
    ):
        websocket_api.async_register_command(hass, command)
