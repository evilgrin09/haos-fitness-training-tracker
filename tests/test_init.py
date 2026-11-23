"""Integration tests for Gym Tracker inside Home Assistant."""

from pathlib import Path

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.gym_tracker.const import DB_FILE, DOMAIN, EXPORT_URL, PANEL_URL_PATH
from custom_components.gym_tracker.store import GymStore


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom integrations."""
    return


@pytest.fixture
async def entry(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, tmp_path: Path, hass_admin_user
) -> MockConfigEntry:
    """Set up the integration in a temporary config directory; the admin user gets the template plan."""
    freezer.move_to("2026-11-11 17:00:00+00:00")  # a Wednesday
    await hass.config.async_set_time_zone("Europe/Berlin")
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, options={"show_panel": True, "panel_title": "Training"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    store: GymStore = hass.data[DOMAIN]
    await store.async_write(lambda db: db.create_user_plan(hass_admin_user.id, dt_util.utcnow(), template=True))
    await hass.async_block_till_done()
    return entry


async def _ws(client, msg_type: str, **data):
    await client.send_json_auto_id({"type": f"{DOMAIN}/{msg_type}", **data})
    response = await client.receive_json()
    assert response["success"], response
    return response["result"]


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    """State of the sensor ``key`` of the only user with a plan."""
    (entity,) = [
        e
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.unique_id.endswith(f"_{key}")
    ]
    return hass.states.get(entity.entity_id).state


async def test_setup_creates_database_panel_and_sensors(hass: HomeAssistant, entry, tmp_path: Path) -> None:
    assert (tmp_path / DB_FILE).exists()
    assert hass.data["frontend_panels"][PANEL_URL_PATH].config["_panel_custom"]["name"] == "gym-tracker-panel"
    assert hass.data["frontend_panels"][PANEL_URL_PATH].sidebar_title == "Training"
    assert _state(hass, entry, "last_workout") == "unknown"
    assert _state(hass, entry, "workouts_week") == "0"
    assert _state(hass, entry, "workouts_month") == "0"


async def test_full_workout_flow(hass: HomeAssistant, entry, hass_ws_client, freezer: FrozenDateTimeFactory) -> None:
    client = await hass_ws_client(hass)
    overview = await _ws(client, "overview")
    assert overview["active_session"] is None
    assert overview["today"] == "2026-11-11"
    assert len(overview["plans"][0]["exercises"]) == 10

    session = await _ws(client, "session/start")
    assert session["status"] == "running"
    assert session["warmup"].startswith("Ausfallschritte")
    session = await _ws(client, "session/warmup", session_id=session["id"], done=True)
    assert session["warmup_done"] is True

    press, curl = session["items"][0], session["items"][1]
    assert press["defaults"] == {"sets": 3, "reps": 12, "seconds": None, "weight": 63}
    freezer.tick(300)
    session = await _ws(
        client, "entry/save", session_id=session["id"], exercise_id=press["exercise"]["id"],
        sets=3, reps=14, weight=65.5, rpe=7, note=" lief gut ",
    )  # fmt: skip
    assert session["items"][0]["entry"]["note"] == "lief gut"
    curl_id = curl["exercise"]["id"]
    session = await _ws(client, "entry/save", session_id=session["id"], exercise_id=curl_id, skipped=True)
    assert session["summary"]["skipped"] == 1

    # Closing and reopening the app: the running session is offered again.
    overview = await _ws(await hass_ws_client(hass), "overview")
    assert overview["active_session"]["id"] == session["id"]
    assert overview["active_session"]["items"][0]["entry"]["weight"] == 65.5
    assert _state(hass, entry, "workouts_week") == "0"

    freezer.tick(1500)
    finished = await _ws(client, "session/finish", session_id=session["id"])
    await hass.async_block_till_done()
    assert finished["status"] == "completed"
    assert finished["summary"]["duration_s"] == 1800
    assert finished["summary"]["weight_changes"][0]["delta"] == 2.5
    assert _state(hass, entry, "last_workout") == "2026-11-11"
    assert _state(hass, entry, "workouts_week") == "1"
    assert _state(hass, entry, "workouts_month") == "1"

    # The next session starts with the adjusted weight.
    freezer.move_to("2026-11-13 17:00:00+00:00")
    second = await _ws(client, "session/start")
    assert second["items"][0]["defaults"]["weight"] == 65.5
    assert second["items"][1]["defaults"]["weight"] == 27
    assert (await _ws(client, "session/cancel", session_id=second["id"])) == {"kept": False}

    sessions = await _ws(client, "sessions")
    assert [s["id"] for s in sessions] == [finished["id"]]
    stats = await _ws(client, "stats/exercise", exercise_id=press["exercise"]["id"], range="4w")
    assert stats["points"][0]["records"] == ["weight", "volume", "reps"]
    overall = await _ws(client, "stats/overview", range="all")
    assert overall["totals"]["count"] == 1

    # Correct an entry afterwards, then delete everything.
    edited = await _ws(
        client, "entry/save", session_id=finished["id"], exercise_id=press["exercise"]["id"],
        sets=3, reps=12, weight=64, rpe=8,
    )  # fmt: skip
    assert edited["items"][0]["entry"]["weight"] == 64
    edited = await _ws(client, "session/update", session_id=finished["id"], note="Notiz")
    assert edited["note"] == "Notiz"
    edited = await _ws(client, "entry/delete", entry_id=edited["items"][1]["entry"]["id"])
    assert edited["items"][1]["entry"] is None
    await _ws(client, "session/delete", session_id=finished["id"])
    await hass.async_block_till_done()
    assert _state(hass, entry, "workouts_week") == "0"
    assert _state(hass, entry, "last_workout") == "unknown"


async def test_session_date_uses_local_time_zone(hass: HomeAssistant, entry, hass_ws_client, freezer) -> None:
    client = await hass_ws_client(hass)
    freezer.move_to("2026-11-11 23:30:00+00:00")  # already Thursday in Berlin
    session = await _ws(client, "session/start")
    assert session["local_date"] == "2026-11-12"


async def test_errors_are_reported(hass: HomeAssistant, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/session/get", "session_id": 42})
    response = await client.receive_json()
    assert not response["success"]
    assert response["error"]["code"] == "not_found"
    session = await _ws(client, "session/start")
    await client.send_json_auto_id({"type": f"{DOMAIN}/session/finish", "session_id": session["id"]})
    assert (await client.receive_json())["error"]["code"] == "empty_session"


async def test_week_counter_resets_on_new_week(hass: HomeAssistant, entry, hass_ws_client, freezer) -> None:
    client = await hass_ws_client(hass)
    session = await _ws(client, "session/start")
    plank_id = session["items"][3]["exercise"]["id"]
    await _ws(client, "entry/save", session_id=session["id"], exercise_id=plank_id, sets=3, seconds=30, rpe=5)
    await _ws(client, "session/finish", session_id=session["id"])
    await hass.async_block_till_done()
    assert _state(hass, entry, "workouts_week") == "1"
    freezer.move_to("2026-11-15 23:00:11+00:00")  # Monday 00:00:11 in Berlin
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _state(hass, entry, "workouts_week") == "0"
    assert _state(hass, entry, "workouts_month") == "1"


async def test_csv_export(hass: HomeAssistant, entry, hass_ws_client, hass_client) -> None:
    client = await hass_ws_client(hass)
    session = await _ws(client, "session/start")
    press_id = session["items"][0]["exercise"]["id"]
    await _ws(client, "entry/save", session_id=session["id"], exercise_id=press_id, sets=2, reps=14, weight=63, rpe=7)
    response = await (await hass_client()).get(EXPORT_URL)
    assert response.status == 200
    assert response.headers["Content-Disposition"] == 'attachment; filename="gym-tracker-2026-11-11.csv"'
    text = (await response.read()).decode("utf-8-sig")
    assert text.count("Beinpresse") == 2
    assert text.startswith("Datum;")


async def test_data_survives_reload_and_removal(hass: HomeAssistant, entry, hass_ws_client, tmp_path: Path) -> None:
    client = await hass_ws_client(hass)
    session = await _ws(client, "session/start")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    overview = await _ws(client, "overview")
    assert overview["active_session"]["id"] == session["id"]
    assert len(overview["plans"]) == 1
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert PANEL_URL_PATH not in hass.data["frontend_panels"]
    assert (tmp_path / DB_FILE).exists()


async def test_plan_editing_and_spontaneous_exercise(hass: HomeAssistant, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    new = {"name": "Wadenheben", "kind": "weight", "target_sets": 3, "reps_min": 12, "reps_max": 14, "start_weight": 40}
    plans = await _ws(client, "exercise/add", **new, muscle_group="Beine & Rumpf", setting="Stufe 3")
    names = [e["name"] for e in plans[0]["exercises"]]
    assert names.index("Wadenheben") == 4
    calf = plans[0]["exercises"][4]
    assert (calf["kind"], calf["start_weight"], calf["setting"]) == ("weight", 40, "Stufe 3")

    plans = await _ws(client, "exercise/update", exercise_id=calf["id"], **{**new, "target_sets": 4})
    assert plans[0]["exercises"][-1]["name"] == "Wadenheben"  # group removed: end of plan
    assert plans[0]["exercises"][-1]["target_sets"] == 4
    plans = await _ws(client, "exercise/move", exercise_id=plans[0]["exercises"][0]["id"], direction="down")
    assert [e["name"] for e in plans[0]["exercises"]][:2] == ["Beinbeuger (liegend)", "Beinpresse"]
    plans = await _ws(client, "exercise/move_group", group="Arme", direction="up")
    assert plans[0]["exercises"][4]["muscle_group"] == "Arme"
    plans = await _ws(client, "exercise/archive", exercise_id=calf["id"])
    assert [e["name"] for e in plans[0]["archived_exercises"]] == ["Wadenheben"]
    assert [e["position"] for e in plans[0]["exercises"]] == list(range(1, 11))

    await client.send_json_auto_id({"type": f"{DOMAIN}/exercise/add", **new})
    assert (await client.receive_json())["error"]["code"] == "name_archived"
    await client.send_json_auto_id({"type": f"{DOMAIN}/exercise/add", **{**new, "name": "Neu", "reps_min": 20}})
    assert (await client.receive_json())["error"]["code"] == "invalid_values"

    # Spontaneous exercises: one new, one taken from the archive.
    session = await _ws(client, "session/start")
    await client.send_json_auto_id({"type": f"{DOMAIN}/exercise/restore", "exercise_id": calf["id"]})
    assert (await client.receive_json())["error"]["code"] == "session_running"
    added = await _ws(client, "session/add_exercise", session_id=session["id"], name="Seitstütz", kind="time",
                      target_sets=2, duration_s=45)  # fmt: skip
    reused = await _ws(client, "session/add_exercise", session_id=session["id"], exercise_id=calf["id"])
    assert reused["exercise_id"] == calf["id"]
    items = reused["session"]["items"]
    assert [(i["exercise"]["name"], i["adhoc"]) for i in items[-2:]] == [("Seitstütz", True), ("Wadenheben", True)]
    side = added["exercise_id"]
    await _ws(client, "entry/save", session_id=session["id"], exercise_id=side, sets=2, seconds=45, rpe=6)
    await _ws(client, "entry/save", session_id=session["id"], exercise_id=calf["id"], sets=4, reps=14, weight=40, rpe=7)
    finished = await _ws(client, "session/finish", session_id=session["id"], adopt=[calf["id"]])
    assert finished["summary"]["done"] == 2
    overview = await _ws(client, "overview")
    plan = overview["plans"][0]
    assert plan["exercises"][-1]["name"] == "Wadenheben"
    assert [e["name"] for e in plan["archived_exercises"]] == ["Seitstütz"]
    assert plan["archived_exercises"][0]["entry_count"] == 1
    stats = await _ws(client, "stats/exercise", exercise_id=added["exercise_id"], range="all")
    assert stats["exercise"]["archived"] is True
    assert len(stats["points"]) == 1


async def test_cardio_commands_and_sensor(hass: HomeAssistant, entry, hass_ws_client, hass_client) -> None:
    client = await hass_ws_client(hass)
    session = await _ws(client, "session/start")
    session = await _ws(client, "cardio/add", session_id=session["id"], phase="warmup", device="bike", minutes=5)
    session = await _ws(client, "cardio/add", session_id=session["id"], phase="warmup", device="treadmill", minutes=5)
    first = session["cardio"]["warmup"][0]["id"]
    session = await _ws(client, "cardio/update", cardio_id=first, device="crosstrainer", minutes=7)
    assert [(c["device"], c["minutes"]) for c in session["cardio"]["warmup"]] == [("crosstrainer", 7), ("treadmill", 5)]
    session = await _ws(client, "cardio/add", session_id=session["id"], phase="cooldown", device="bike", minutes=10)
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/cardio/add", "session_id": session["id"], "phase": "warmup", "device": "bike", "minutes": 0}
    )
    assert not (await client.receive_json())["success"]
    finished = await _ws(client, "session/finish", session_id=session["id"])
    assert finished["summary"]["cardio"]["minutes"] == 22
    await hass.async_block_till_done()
    assert _state(hass, entry, "cardio_week") == "22"
    assert _state(hass, entry, "workouts_week") == "1"
    stats = await _ws(client, "stats/overview", range="4w")
    assert stats["cardio"]["by_phase"] == {"warmup": 12, "cooldown": 10}
    session = await _ws(client, "cardio/delete", cardio_id=first)
    await hass.async_block_till_done()
    assert _state(hass, entry, "cardio_week") == "15"
    text = await (await (await hass_client()).get(EXPORT_URL)).text()
    assert text.count(";Cardio;") == 2


async def test_recommendation_decision(hass: HomeAssistant, entry, hass_ws_client, freezer) -> None:
    client = await hass_ws_client(hass)
    for day, reps in (("2026-11-09", 13), ("2026-11-10", 14)):
        freezer.move_to(f"{day} 17:00:00+00:00")
        session = await _ws(client, "session/start")
        press = session["items"][0]["exercise"]["id"]
        values = {"sets": 3, "reps": reps, "weight": 63, "rpe": 7}
        await _ws(client, "entry/save", session_id=session["id"], exercise_id=press, **values)
        await _ws(client, "session/finish", session_id=session["id"])
    freezer.move_to("2026-11-11 17:00:00+00:00")
    session = await _ws(client, "session/start")
    item = session["items"][0]
    assert item["recommendation"]["rule"] == "weight"
    assert item["recommendation"]["data"]["suggested_weight"] == 65.5
    session = await _ws(
        client, "recommendation/decide", session_id=session["id"], exercise_id=press, decision="accepted"
    )
    assert session["items"][0]["defaults"] == {"sets": 3, "reps": 12, "seconds": None, "weight": 65.5}
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/recommendation/decide", "session_id": session["id"], "exercise_id": 999,
         "decision": "declined"}
    )
    assert (await client.receive_json())["error"]["code"] == "not_found"
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/entry/save", "session_id": session["id"], "exercise_id": press, "sets": 3, "reps": 12,
         "weight": 65.5}
    )  # fmt: skip
    assert not (await client.receive_json())["success"]  # RPE is required


async def test_measured_seconds_per_set(hass: HomeAssistant, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    session = await _ws(client, "session/start")
    plank = next(i for i in session["items"] if i["exercise"]["name"] == "Plank")["exercise"]["id"]
    session = await _ws(
        client, "entry/save", session_id=session["id"], exercise_id=plank, seconds_per_set=[32, 30, 28], rpe=7
    )
    entry = next(i for i in session["items"] if i["exercise"]["id"] == plank)["entry"]
    assert [s["seconds"] for s in entry["sets"]] == [32, 30, 28]
    await client.send_json_auto_id(
        {"type": f"{DOMAIN}/entry/save", "session_id": session["id"], "exercise_id": plank,
         "seconds_per_set": [], "rpe": 7}
    )  # fmt: skip
    assert not (await client.receive_json())["success"]
