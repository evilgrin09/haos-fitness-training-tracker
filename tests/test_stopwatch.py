"""Seconds per set (stopwatch) in storage, statistics and suggestions."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.gym_tracker import logic
from custom_components.gym_tracker.db import GymDatabase, GymError

from .helpers import USER, UserDB

START = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)
TODAY = date(2026, 11, 23)
TIMED = {"target_sets": 3, "reps_min": None, "reps_max": None, "duration_s": 30, "weighted": False}


@pytest.fixture
def db(tmp_path: Path) -> UserDB:
    """Database with the default plan of one user."""
    database = GymDatabase(tmp_path / "gym.db")
    database.create_user_plan(USER, START, template=True)
    yield UserDB(database)
    database.close()


def _ex(db: UserDB, name: str) -> dict:
    return next(e for e in db.get_plans()[0]["exercises"] if e["name"] == name)


def _item(db: UserDB, session_id: int, name: str) -> dict:
    return next(i for i in db.session_detail(session_id)["items"] if i["exercise"]["name"] == name)


def _plank(db: UserDB, day: int, per_set: list[int], rpe: int = 7, finish: bool = True) -> int:
    when = START + timedelta(days=day)
    session_id = db.start_session(None, when, when.date())
    db.save_entry(session_id, _ex(db, "Plank")["id"], when + timedelta(minutes=5), seconds_per_set=per_set, rpe=rpe)
    if finish:
        db.finish_session(session_id, when + timedelta(minutes=30))
    else:
        db.cancel_session(session_id, when + timedelta(minutes=30))
    return session_id


# ------------------------------------------------------------------ storage


def test_measured_sets_are_stored_one_by_one(db: UserDB) -> None:
    session_id = _plank(db, 0, [32, 30, 28, 31])
    entry = _item(db, session_id, "Plank")["entry"]
    assert [s["seconds"] for s in entry["sets"]] == [32, 30, 28, 31]
    assert all(s["reps"] is None and s["weight"] is None for s in entry["sets"])
    assert (entry["set_count"], entry["min_seconds"], entry["max_seconds"], entry["avg_seconds"]) == (4, 28, 32, 30.25)
    assert entry["volume"] == 0
    rows = [line.split(";") for line in db.export_csv().strip().split("\r\n")[1:]]
    assert [(row[7], row[9]) for row in rows] == [("1", "32"), ("2", "30"), ("3", "28"), ("4", "31")]


def test_saving_again_replaces_the_measured_sets(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    plank = _ex(db, "Plank")["id"]
    db.save_entry(session_id, plank, START, seconds_per_set=[20, 25], rpe=6)
    db.save_entry(session_id, plank, START, seconds_per_set=[33], rpe=8, sets=3, seconds=99)
    entry = _item(db, session_id, "Plank")["entry"]
    # The list wins over sets/seconds.
    assert ([s["seconds"] for s in entry["sets"]], entry["rpe"]) == ([33], 8)
    # Manual entry as before still works.
    db.save_entry(session_id, plank, START, sets=2, seconds=40, rpe=7)
    assert [s["seconds"] for s in _item(db, session_id, "Plank")["entry"]["sets"]] == [40, 40]


@pytest.mark.parametrize("per_set", [[], [30] * 21, [30, 3601], [-1]])
def test_measured_sets_validation(db: UserDB, per_set: list[int]) -> None:
    session_id = db.start_session(None, START, START.date())
    with pytest.raises(GymError):
        db.save_entry(session_id, _ex(db, "Plank")["id"], START, seconds_per_set=per_set, rpe=7)


def test_seconds_per_set_only_for_time_exercises(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    with pytest.raises(GymError):
        db.save_entry(session_id, _ex(db, "Beinpresse")["id"], START, seconds_per_set=[30], weight=63, rpe=7)


# --------------------------------------------------------------- statistics


def test_statistics_with_different_seconds_per_set(db: UserDB) -> None:
    _plank(db, 0, [30, 30, 30])
    _plank(db, 3, [45, 30, 27])
    points = db.exercise_stats(_ex(db, "Plank")["id"], None)["points"]
    assert [p["avg_seconds"] for p in points] == [30, 34]
    assert [p["min_seconds"] for p in points] == [30, 27]
    records = db.exercise_stats(_ex(db, "Plank")["id"], None)["records"]
    # The record is the longest single set.
    assert (records["seconds"]["value"], records["seconds"]["date"]) == (45, "2026-11-05")


def test_stagnation_compares_the_average_per_set() -> None:
    def point(days_ago: int, per_set: list[int]) -> dict:
        summary = logic.summarize_sets([{"seconds": value} for value in per_set])
        return {**summary, "date": (TODAY - timedelta(days=days_ago)).isoformat(), "rpe": 6}

    same_average = [point(15, [30, 30, 30]), *[point(days, [40, 30, 20]) for days in (12, 9, 6, 3, 0)]]
    assert logic.stagnation("time", same_average, TODAY) is not None
    longer = [*same_average[:-1], point(0, [40, 35, 20])]
    assert logic.stagnation("time", longer, TODAY) is None


# -------------------------------------------------------------- suggestions


def _point(per_set: list[int], rpe: int, days_ago: int) -> dict:
    summary = logic.summarize_sets([{"seconds": value} for value in per_set])
    return {**summary, "date": (TODAY - timedelta(days=days_ago)).isoformat(), "rpe": rpe}


def test_time_reached_means_reached_in_every_set() -> None:
    before = _point([30, 30, 30], 7, 5)
    assert logic.recommend(TIMED, [before, _point([45, 40, 29], 6, 2)], TODAY) is None
    result = logic.recommend(TIMED, [before, _point([45, 40, 32], 6, 2)], TODAY)
    # The next step starts from the shortest set.
    assert (result["rule"], result["data"]["suggested_seconds"]) == ("time", 37)


def test_time_suggestion_from_measured_sets_in_the_database(db: UserDB) -> None:
    _plank(db, 0, [30, 30, 30])
    _plank(db, 3, [41, 35, 33], rpe=6)
    session_id = db.start_session(None, START + timedelta(days=5), date(2026, 11, 7))
    item = _item(db, session_id, "Plank")
    # Prefilled with the time held in every set.
    assert item["defaults"]["seconds"] == 33
    assert (item["recommendation"]["rule"], item["recommendation"]["data"]["suggested_seconds"]) == ("time", 38)


# ----------------------------------------- suggestion vs. prefill (cancelled)


def _press(db: UserDB, day: int, weight: float, reps: int, finish: bool = True) -> None:
    when = START + timedelta(days=day)
    session_id = db.start_session(None, when, when.date())
    db.save_entry(session_id, _ex(db, "Beinpresse")["id"], when, sets=3, reps=reps, weight=weight, rpe=7)
    if finish:
        db.finish_session(session_id, when + timedelta(minutes=30))
    else:
        db.cancel_session(session_id, when + timedelta(minutes=30))


def test_no_suggestion_below_the_prefill_after_a_cancelled_session(db: UserDB) -> None:
    _press(db, 0, 73, 13)
    _press(db, 2, 73, 14)
    # Already trained with the suggested weight in a cancelled session.
    _press(db, 4, 75.5, 12, finish=False)
    session_id = db.start_session(None, START + timedelta(days=6), date(2026, 11, 8))
    item = _item(db, session_id, "Beinpresse")
    assert item["defaults"]["weight"] == 75.5
    assert item["recommendation"] is None


def test_suggestion_above_the_prefill_stays(db: UserDB) -> None:
    _press(db, 0, 73, 13)
    _press(db, 2, 73, 14)
    _press(db, 4, 70, 12, finish=False)  # lighter in the cancelled session
    session_id = db.start_session(None, START + timedelta(days=6), date(2026, 11, 8))
    item = _item(db, session_id, "Beinpresse")
    assert item["defaults"]["weight"] == 70
    assert item["recommendation"]["data"]["suggested_weight"] == 75.5


@pytest.mark.parametrize(
    ("rule", "data", "prefill", "dropped"),
    [
        ("weight", {"suggested_weight": 65.5}, {"weight": 65.5}, True),
        ("weight_double", {"suggested_weight": 68}, {"weight": 65.5}, False),
        ("reps", {"suggested_reps": 13}, {"reps": 13}, True),
        ("reps", {"suggested_reps": 13}, {"reps": 12}, False),
        ("time", {"suggested_seconds": 40}, {"seconds": 45}, True),
        ("time", {"suggested_seconds": 40}, {"seconds": None}, False),
        ("recovery", {}, {"weight": 100}, False),
    ],
)
def test_superseded(rule: str, data: dict, prefill: dict, dropped: bool) -> None:
    assert logic._superseded({"rule": rule, "data": data}, prefill) is dropped
