"""Several Home Assistant users: migration to tim-mobile, options, access and separation."""

import logging
from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import CLIENT_ID, MockConfigEntry

from custom_components.gym_tracker.const import DB_FILE, DOMAIN, EXPORT_URL

from .helpers import make_v021_database, table_counts

OLD_SENSORS = {
    "last_workout": "sensor.gym_tracker_letztes_training",
    "workouts_week": "sensor.gym_tracker_trainings_diese_woche",
    "workouts_month": "sensor.gym_tracker_trainings_diesen_monat",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations."""
    return


@pytest.fixture
async def base(hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path) -> Path:
    """Home Assistant with http and a temporary config directory."""
    freezer.move_to("2026-11-11 17:00:00+00:00")
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "http", {})
    return tmp_path


async def _user(hass: HomeAssistant, name: str, admin: bool = False):
    """A Home Assistant user with an access token."""
    user = await hass.auth.async_create_user(name, group_ids=["system-admin" if admin else "system-users"])
    refresh = await hass.auth.async_create_refresh_token(user, CLIENT_ID)
    return user, hass.auth.async_create_access_token(refresh)


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, options={"show_panel": True, "panel_title": "Gym Tracker", **options})
    entry.add_to_hass(hass)
    _register_old_sensors(hass, entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _register_old_sensors(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """The sensors of 0.2.1 as they exist in the entity registry of a German installation."""
    registry = er.async_get(hass)
    for key, entity_id in OLD_SENSORS.items():
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}", suggested_object_id=entity_id.split(".")[1], config_entry=entry
        )


async def _ws(client, msg_type: str, **data):
    await client.send_json_auto_id({"type": f"{DOMAIN}/{msg_type}", **data})
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


async def _ws_error(client, msg_type: str, **data) -> str:
    await client.send_json_auto_id({"type": f"{DOMAIN}/{msg_type}", **data})
    response = await client.receive_json()
    assert not response["success"], response
    return response["error"]["code"]


# --------------------------------------------------------------- migration


async def test_migration_assigns_everything_to_tim_mobile(
    hass: HomeAssistant, base: Path, hass_ws_client, caplog: pytest.LogCaptureFixture
) -> None:
    before = make_v021_database(base / DB_FILE)
    tim, tim_token = await _user(hass, "tim-mobile")
    _other, other_token = await _user(hass, "Anna")
    caplog.set_level(logging.INFO)
    entry = await _setup(hass)

    backup = base / f"{DB_FILE}.bak-0.2.1"
    assert backup.exists()
    assert table_counts(backup) == before
    assert str(backup) in caplog.text
    assert table_counts(base / DB_FILE) == before
    assert entry.data["legacy_user"] == tim.id

    client = await hass_ws_client(hass, tim_token)
    overview = await _ws(client, "overview")
    assert overview["plans"][0]["name"] == "Ganzkörper-Training"
    assert overview["unassigned"] is False
    assert overview["summary"]["total"] == 7
    assert len(await _ws(client, "sessions", limit=100)) == 8
    # The sensors keep their entity ids and now count tim-mobile's trainings.
    assert hass.states.get(OLD_SENSORS["last_workout"]).state == "2026-10-24"
    assert hass.states.get(OLD_SENSORS["workouts_month"]).state == "0"
    device = dr.async_get(hass).async_get_device({(DOMAIN, entry.entry_id)})
    assert device.name == "Gym Tracker tim-mobile"

    # Another user sees none of it.
    anna = await hass_ws_client(hass, other_token)
    overview = await _ws(anna, "overview")
    assert overview["plans"] == []
    assert overview["active_session"] is None
    assert await _ws(anna, "sessions") == []

    # A restart neither migrates nor backs up again.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert list(base.glob("*.bak-*")) == [backup]
    assert hass.states.get(OLD_SENSORS["last_workout"]).state == "2026-10-24"


async def test_owner_not_found_then_assigned_in_options(
    hass: HomeAssistant, base: Path, hass_ws_client, caplog: pytest.LogCaptureFixture
) -> None:
    before = make_v021_database(base / DB_FILE)
    tim, tim_token = await _user(hass, "Tim")  # not "tim-mobile": nothing is guessed
    _admin, admin_token = await _user(hass, "Admin", admin=True)
    entry = await _setup(hass)
    assert "nicht eindeutig gefunden" in caplog.text
    assert "legacy_user" not in entry.data
    assert table_counts(base / DB_FILE) == before

    # Nobody sees the data; admins get the numbers, others a plain hint.
    admin = await hass_ws_client(hass, admin_token)
    assert (await _ws(admin, "overview"))["unassigned"] == {"plans": 1, "sessions": 8}
    client = await hass_ws_client(hass, tim_token)
    overview = await _ws(client, "overview")
    assert (overview["plans"], overview["unassigned"]) == ([], True)
    # The old sensors keep their registry entries but get no values until the data is assigned.
    assert er.async_get(hass).async_get(OLD_SENSORS["workouts_week"]) is not None
    assert hass.states.get(OLD_SENSORS["workouts_week"]) is None

    # The admin picks the owner; users with an own plan are not offered.
    await _ws(admin, "plan/create", template=True)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    owner_key = next(key for key in schema if key == "legacy_owner")
    offered = {option["value"] for option in schema[owner_key].config["options"]}
    assert tim.id in offered
    assert _admin.id not in offered
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"show_panel": True, "panel_title": "Gym Tracker", "allowed_users": [], "legacy_owner": tim.id},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    assert entry.data["legacy_user"] == tim.id
    client = await hass_ws_client(hass, tim_token)
    overview = await _ws(client, "overview")
    assert overview["plans"][0]["name"] == "Ganzkörper-Training"
    assert overview["summary"]["total"] == 7
    assert (await _ws(admin, "overview"))["unassigned"] is False
    assert hass.states.get(OLD_SENSORS["workouts_week"]).state == "0"
    assert table_counts(base / DB_FILE) == before | {"plans": 2, "exercises": before["exercises"] + 10}
    # Without unassigned data the option is not shown any more.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "legacy_owner" not in result["data_schema"].schema


async def test_owner_logging_in_later_gets_the_data(hass: HomeAssistant, base: Path, hass_ws_client) -> None:
    """Home Assistant creates a user only on the first login, possibly after the integration started."""
    before = make_v021_database(base / DB_FILE)
    _other, other_token = await _user(hass, "Anna")
    entry = await _setup(hass)
    anna = await hass_ws_client(hass, other_token)
    assert (await _ws(anna, "overview"))["unassigned"] is True

    _tim, tim_token = await _user(hass, "tim-mobile")
    tim = await hass_ws_client(hass, tim_token)
    overview = await _ws(tim, "overview")
    assert overview["plans"][0]["name"] == "Ganzkörper-Training"
    assert overview["unassigned"] is False
    await hass.async_block_till_done()
    assert entry.data["legacy_user"] == _tim.id
    assert hass.states.get(OLD_SENSORS["last_workout"]).state == "2026-10-24"
    assert table_counts(base / DB_FILE) == before


# --------------------------------------------------------------- access


async def test_allowed_users(hass: HomeAssistant, base: Path, hass_ws_client, hass_client) -> None:
    allowed, allowed_token = await _user(hass, "Anna")
    _blocked, blocked_token = await _user(hass, "Ben")
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    users_key = next(key for key in schema if key == "allowed_users")
    offered = {option["value"] for option in schema[users_key].config["options"]}
    assert {allowed.id, _blocked.id} <= offered
    assert all(not user.system_generated for user in await hass.auth.async_get_users() if user.id in offered)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"show_panel": True, "panel_title": "Gym Tracker", "allowed_users": [allowed.id]}
    )
    await hass.async_block_till_done()

    anna = await hass_ws_client(hass, allowed_token)
    await _ws(anna, "plan/create", template=True)
    ben = await hass_ws_client(hass, blocked_token)
    for msg_type, data in (("overview", {}), ("plan/create", {"template": True}), ("sessions", {})):
        assert await _ws_error(ben, msg_type, **data) == "no_access"
    assert (await (await hass_client(blocked_token)).get(EXPORT_URL)).status == 403
    assert (await (await hass_client(allowed_token)).get(EXPORT_URL)).status == 200

    # Empty list: everybody may use it again.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"show_panel": True, "panel_title": "Gym Tracker", "allowed_users": []}
    )
    await hass.async_block_till_done()
    ben = await hass_ws_client(hass, blocked_token)
    assert (await _ws(ben, "overview"))["plans"] == []


# --------------------------------------------------------------- separation


async def test_users_are_separated(hass: HomeAssistant, base: Path, hass_ws_client, hass_client) -> None:
    anna_user, anna_token = await _user(hass, "Anna")
    ben_user, ben_token = await _user(hass, "Ben")
    entry = await _setup(hass)
    anna = await hass_ws_client(hass, anna_token)
    ben = await hass_ws_client(hass, ben_token)

    # Without a plan: the start screen offers to create one.
    overview = await _ws(anna, "overview")
    assert (overview["plans"], overview["unassigned"], overview["user"]["name"]) == ([], False, "Anna")
    assert await _ws_error(anna, "session/start") == "no_plan"
    plans = await _ws(anna, "plan/create", template=True)
    assert len(plans[0]["exercises"]) == 10
    assert await _ws_error(anna, "plan/create", template=True) == "plan_exists"
    plans = await _ws(ben, "plan/create", name="Ben", freq_min=1, freq_max=2)
    assert (plans[0]["name"], plans[0]["exercises"]) == ("Ben", [])
    deadlift = {"name": "Kreuzheben", "kind": "weight", "target_sets": 3, "reps_min": 5, "reps_max": 8}
    deadlift["start_weight"] = 60
    await _ws(ben, "exercise/add", **deadlift)

    session = await _ws(anna, "session/start")
    press = session["items"][0]["exercise"]["id"]
    await _ws(anna, "entry/save", session_id=session["id"], exercise_id=press, sets=3, reps=14, weight=63, rpe=7)
    entry_id = (await _ws(anna, "session/get", session_id=session["id"]))["items"][0]["entry"]["id"]

    for msg_type, data in (
        ("session/get", {"session_id": session["id"]}),
        ("session/finish", {"session_id": session["id"]}),
        ("session/cancel", {"session_id": session["id"]}),
        ("session/delete", {"session_id": session["id"]}),
        ("session/warmup", {"session_id": session["id"], "done": True}),
        ("session/update", {"session_id": session["id"], "note": "x"}),
        ("session/add_exercise", {"session_id": session["id"], "exercise_id": press}),
        ("entry/save", {"session_id": session["id"], "exercise_id": press, "sets": 1, "reps": 1, "rpe": 5}),
        ("entry/delete", {"entry_id": entry_id}),
        ("stats/exercise", {"exercise_id": press}),
        ("exercise/archive", {"exercise_id": press}),
        ("exercise/restore", {"exercise_id": press}),
        ("exercise/move", {"exercise_id": press, "direction": "down"}),
        ("exercise/update", {"exercise_id": press, **deadlift}),
    ):
        assert await _ws_error(ben, msg_type, **data) == "not_found", msg_type
    # Ben's own training is independent of Anna's running session.
    ben_session = await _ws(ben, "session/start")
    assert ben_session["id"] != session["id"]
    assert [i["exercise"]["name"] for i in ben_session["items"]] == ["Kreuzheben"]

    await _ws(anna, "session/finish", session_id=session["id"])
    await hass.async_block_till_done()
    assert (await _ws(anna, "overview"))["summary"]["total"] == 1
    assert (await _ws(ben, "overview"))["summary"]["total"] == 0
    assert (await _ws(ben, "stats/overview", range="all"))["totals"]["count"] == 0

    anna_csv = await (await (await hass_client(anna_token)).get(EXPORT_URL)).text()
    ben_csv = await (await (await hass_client(ben_token)).get(EXPORT_URL)).text()
    assert anna_csv.count("Beinpresse") == 3
    assert "Beinpresse" not in ben_csv

    # One device with its own sensors per user.
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert sorted(device.name for device in devices) == ["Gym Tracker Anna", "Gym Tracker Ben"]
    assert hass.states.get("sensor.gym_tracker_anna_workouts_this_week").state == "1"
    assert hass.states.get("sensor.gym_tracker_ben_workouts_this_week").state == "0"
    assert hass.states.get("sensor.gym_tracker_anna_last_workout").state == "2026-11-11"
    # The sensors of 0.2.1 stay reserved for the owner of the old data (none here).
    assert hass.states.get(OLD_SENSORS["workouts_week"]) is None
    assert anna_user.id != ben_user.id
