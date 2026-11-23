"""Tests for cardio in the warm-up and cool-down."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.gym_tracker import logic
from custom_components.gym_tracker.db import GymDatabase, GymError

from .helpers import OTHER, USER, UserDB

START = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)  # a Monday


def _error(code: str):
    return pytest.raises(GymError, check=lambda err: err.code == code)


@pytest.fixture
def db(tmp_path: Path) -> UserDB:
    """Database with the default plan of one user."""
    database = GymDatabase(tmp_path / "gym.db")
    database.create_user_plan(USER, START, template=True)
    yield UserDB(database)
    database.close()


def _press(db: UserDB) -> int:
    return db.get_plans()[0]["exercises"][0]["id"]


def _session(db: UserDB, when: datetime, cardio: list[tuple[str, str, int]], strength: bool = True) -> int:
    """A completed session with cardio entries (phase, device, minutes) and optionally the leg press."""
    session_id = db.start_session(None, when, when.date())
    for offset, (phase, device, minutes) in enumerate(cardio):
        db.add_cardio(session_id, phase, device, minutes, when + timedelta(minutes=offset))
    if strength:
        db.save_entry(session_id, _press(db), when + timedelta(minutes=20), sets=3, reps=14, weight=63, rpe=7)
    db.finish_session(session_id, when + timedelta(minutes=60))
    return session_id


def test_several_entries_per_phase(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    first = db.add_cardio(session_id, "warmup", "bike", 5, START)
    db.add_cardio(session_id, "warmup", "treadmill", 5, START + timedelta(minutes=5))
    db.add_cardio(session_id, "cooldown", "crosstrainer", 10, START + timedelta(minutes=50))
    detail = db.session_detail(session_id)
    warmup = detail["cardio"]["warmup"]
    assert [(c["device"], c["minutes"], c["position"]) for c in warmup] == [("bike", 5, 1), ("treadmill", 5, 2)]
    assert [(c["device"], c["minutes"]) for c in detail["cardio"]["cooldown"]] == [("crosstrainer", 10)]
    assert detail["summary"]["cardio"] == {
        "minutes": 20,
        "by_phase": {"warmup": 10, "cooldown": 10},
        "by_device": {"crosstrainer": 10, "bike": 5, "treadmill": 5},
    }
    # Change and delete; the order in the phase stays without gaps.
    assert db.update_cardio(first, "crosstrainer", 8) == session_id
    assert db.delete_cardio(first) == session_id
    warmup = db.session_detail(session_id)["cardio"]["warmup"]
    assert [(c["device"], c["minutes"], c["position"]) for c in warmup] == [("treadmill", 5, 1)]
    with _error("not_found"):
        db.delete_cardio(first)


def test_cardio_does_not_count_as_volume_intensity_or_record(db: UserDB) -> None:
    session_id = _session(db, START, [("warmup", "bike", 10), ("cooldown", "treadmill", 5)])
    summary = db.session_detail(session_id)["summary"]
    assert summary["volume"] == 3 * 14 * 63
    assert summary["avg_rpe"] == 7
    assert summary["done"] == 1
    records = db.exercise_stats(_press(db), None)["records"]
    assert set(records) == {"weight", "volume", "reps"}


@pytest.mark.parametrize(
    ("phase", "device", "minutes"),
    [
        ("middle", "bike", 5),
        ("warmup", "rowing", 5),
        ("warmup", "bike", 0),
        ("warmup", "bike", 181),
        ("warmup", "bike", 2.5),
    ],
)
def test_cardio_validation(db: UserDB, phase: str, device: str, minutes) -> None:
    session_id = db.start_session(None, START, START.date())
    with _error("invalid_values"):
        db.add_cardio(session_id, phase, device, minutes, START)
    entry = db.add_cardio(session_id, "warmup", "bike", 180, START)
    with _error("invalid_values"):
        db.update_cardio(entry, device if device != "bike" else "treadmill", minutes if phase == "warmup" else 0)


def test_cardio_uuid_is_idempotent(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    first = db.add_cardio(session_id, "warmup", "bike", 5, START, "abc")
    assert db.add_cardio(session_id, "warmup", "bike", 5, START, "abc") == first
    assert len(db.session_detail(session_id)["cardio"]["warmup"]) == 1


def test_session_with_cardio_only(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    with _error("empty_session"):
        db.finish_session(session_id, START)
    db.add_cardio(session_id, "warmup", "bike", 20, START + timedelta(minutes=20))
    db.finish_session(session_id, START + timedelta(minutes=25))
    detail = db.session_detail(session_id)
    assert (detail["status"], detail["summary"]["done"], detail["summary"]["cardio"]["minutes"]) == ("completed", 0, 20)
    assert db.summary(START.date())["week"] == 1
    # A cancelled session with cardio only is kept, ending at the last cardio entry if finished much later.
    other = db.start_session(None, START + timedelta(days=1), date(2026, 11, 3))
    db.add_cardio(other, "warmup", "treadmill", 5, START + timedelta(days=1, minutes=5))
    assert db.cancel_session(other, START + timedelta(days=1, hours=5)) is True
    assert db.session_detail(other)["summary"]["duration_s"] == 300


def test_cardio_can_be_added_to_past_sessions(db: UserDB) -> None:
    session_id = _session(db, START, [])
    db.add_cardio(session_id, "cooldown", "treadmill", 7, START + timedelta(days=3))
    assert db.list_sessions()[0]["cardio_minutes"] == 7
    # Adding afterwards does not change the duration of the session.
    assert db.session_detail(session_id)["summary"]["duration_s"] == 3600


def test_cardio_sensor_figure_counts_completed_sessions_this_week(db: UserDB) -> None:
    _session(db, START - timedelta(days=1), [("warmup", "bike", 30)])  # Sunday of the previous week
    _session(db, START, [("warmup", "bike", 10), ("cooldown", "treadmill", 5)])
    running = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    db.add_cardio(running, "warmup", "crosstrainer", 12, START + timedelta(days=2))
    assert db.summary(date(2026, 11, 4))["cardio_week"] == 15
    assert db.summary(date(2026, 11, 9))["cardio_week"] == 0


def test_cardio_statistics(db: UserDB) -> None:
    _session(db, START, [("warmup", "bike", 10), ("cooldown", "treadmill", 5)])
    _session(db, START + timedelta(days=2), [("warmup", "bike", 5), ("warmup", "crosstrainer", 5)])
    _session(db, START + timedelta(days=7), [], strength=True)
    _session(db, START + timedelta(days=9), [("cooldown", "crosstrainer", 15)])
    stats = db.overview_stats(date(2026, 11, 2), date(2026, 11, 15))
    cardio = stats["cardio"]
    assert (cardio["minutes"], cardio["by_phase"]) == (40, {"warmup": 20, "cooldown": 20})
    assert cardio["by_device"] == {"crosstrainer": 20, "bike": 15, "treadmill": 5}
    # 40 minutes of 4 sessions à 60 minutes.
    assert cardio["share"] == round(40 / 240, 4)
    assert [(b["start"], b["minutes"]) for b in cardio["buckets"]] == [("2026-11-02", 25), ("2026-11-09", 15)]
    assert cardio["buckets"][0]["by_device"] == {"crosstrainer": 5, "bike": 15, "treadmill": 5}
    assert [(p["date"], p["minutes"]) for p in cardio["sessions"]] == [
        ("2026-11-02", 15), ("2026-11-04", 10), ("2026-11-11", 15),
    ]  # fmt: skip
    # Volume and intensity of the strength part are unchanged by cardio.
    assert stats["totals"]["volume"] == 4 * 3 * 14 * 63
    assert stats["totals"]["avg_rpe"] == 7


def test_cardio_statistics_by_month_and_empty() -> None:
    rows = [{"phase": "warmup", "device": "bike", "minutes": 10, "date": "2026-01-31"}]
    sessions = [{"id": 1, "date": "2026-01-31", "duration_s": 0, "cardio_minutes": 10}]
    stats = logic.cardio_stats(rows, sessions, date(2025, 12, 1), date(2026, 2, 1), "month")
    assert [b["minutes"] for b in stats["buckets"]] == [0, 10, 0]
    assert stats["share"] is None
    empty = logic.cardio_stats([], [], date(2026, 1, 1), date(2026, 1, 7), "week")
    assert (empty["minutes"], empty["sessions"]) == (0, [])


def test_cardio_in_csv(db: UserDB) -> None:
    _session(db, START, [("warmup", "bike", 10), ("cooldown", "treadmill", 5)])
    lines = db.export_csv().strip().split("\r\n")
    assert [line.split(";")[5] for line in lines[1:]] == ["Cardio", "Beinpresse", "Beinpresse", "Beinpresse", "Cardio"]
    assert lines[1].split(";")[-3:] == ["Aufwärmen", "Fahrrad", "10"]
    assert lines[-1].split(";")[-3:] == ["Auslaufen", "Laufband", "5"]


def test_cardio_of_other_users_is_hidden(db: UserDB) -> None:
    session_id = _session(db, START, [("warmup", "bike", 10)])
    entry = db.session_detail(session_id)["cardio"]["warmup"][0]["id"]
    db.raw.create_user_plan(OTHER, START, template=True)
    other = UserDB(db.raw, OTHER)
    for change in (
        lambda: other.add_cardio(session_id, "warmup", "bike", 5, START),
        lambda: other.update_cardio(entry, "bike", 5),
        lambda: other.delete_cardio(entry),
    ):
        with _error("not_found"):
            change()
    assert other.summary(START.date())["cardio_week"] == 0
    assert ";Cardio;" not in other.export_csv()
    assert db.session_detail(session_id)["cardio"]["warmup"][0]["minutes"] == 10


def test_deleting_a_session_deletes_its_cardio(db: UserDB) -> None:
    session_id = _session(db, START, [("warmup", "bike", 10)])
    db.delete_session(session_id)
    assert db.raw._conn.execute("SELECT COUNT(*) FROM cardio_entries").fetchone()[0] == 0
