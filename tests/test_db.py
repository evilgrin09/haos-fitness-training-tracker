"""Tests for the SQLite storage and the weight logic on real data."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from custom_components.gym_tracker.db import GymDatabase, GymError

START = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)  # a Monday


@pytest.fixture
def db(tmp_path: Path) -> GymDatabase:
    """Database with the default plan."""
    database = GymDatabase(tmp_path / "gym.db")
    assert database.seed_default_plan(START)
    yield database
    database.close()


def _exercise(db: GymDatabase, name: str) -> dict:
    return next(e for e in db.get_plans()[0]["exercises"] if e["name"] == name)


def _item(db: GymDatabase, session_id: int, name: str) -> dict:
    return next(i for i in db.session_detail(session_id)["items"] if i["exercise"]["name"] == name)


def _workout(db: GymDatabase, when: datetime, weights: dict[str, float], *, finish: bool = True, rpe: int = 7) -> int:
    """Record a session with the given exercises (name -> weight)."""
    session_id = db.start_session(None, when, when.date())
    for offset, (name, weight) in enumerate(weights.items()):
        exercise = _exercise(db, name)
        db.save_entry(
            session_id, exercise["id"], when + timedelta(minutes=5 * (offset + 1)),
            sets=exercise["target_sets"], reps=exercise["reps_max"], seconds=exercise["duration_s"],
            weight=weight if exercise["weighted"] else None, rpe=rpe,
        )  # fmt: skip
    if finish:
        db.finish_session(session_id, when + timedelta(minutes=60))
    return session_id


def test_seed_creates_plan_once(db: GymDatabase) -> None:
    assert not db.seed_default_plan(START)
    (plan,) = db.get_plans()
    assert plan["name"] == "Ganzkörper-Training"
    assert (plan["freq_min"], plan["freq_max"]) == (2, 3)
    assert plan["warmup"].startswith("Ausfallschritte")
    names = [e["name"] for e in plan["exercises"]]
    assert len(names) == 10
    assert names[0] == "Beinpresse"
    assert names[-1] == "Trizeps Extensions"
    press, plank, raises = plan["exercises"][0], plan["exercises"][3], plan["exercises"][7]
    assert (press["target_sets"], press["reps_min"], press["reps_max"], press["start_weight"]) == (3, 12, 14, 63)
    assert press["setting"] == "Sitz 6–7"
    assert (plank["duration_s"], plank["reps_max"], plank["weighted"]) == (30, None, False)
    assert (raises["target_sets"], raises["reps_min"], raises["reps_max"], raises["start_weight"]) == (2, 15, 15, 3)
    assert not _exercise(db, "Rückenstrecker")["weighted"]


def test_database_persists_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "gym.db"
    first = GymDatabase(path)
    first.seed_default_plan(START)
    session_id = _workout(first, START, {"Beinpresse": 65})
    first.close()
    second = GymDatabase(path)
    assert not second.seed_default_plan(START)
    assert second.session_detail(session_id)["status"] == "completed"
    assert _item(second, session_id, "Beinpresse")["entry"]["weight"] == 65
    second.close()


def test_first_training_uses_plan_weight(db: GymDatabase) -> None:
    session_id = db.start_session(None, START, START.date())
    item = _item(db, session_id, "Beinpresse")
    assert item["previous"] is None
    assert item["defaults"] == {"sets": 3, "reps": 14, "seconds": None, "weight": 63}
    assert _item(db, session_id, "Plank")["defaults"] == {"sets": 3, "reps": None, "seconds": 30, "weight": None}


def test_last_recorded_weight_becomes_default(db: GymDatabase) -> None:
    _workout(db, START, {"Beinpresse": 65.5, "Beinbeuger (liegend)": 27})
    second = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    press = _item(db, second, "Beinpresse")
    assert press["defaults"]["weight"] == 65.5
    assert press["previous"]["weight"] == 65.5
    assert press["previous"]["date"] == "2026-11-02"
    # Untouched exercises keep the plan weight.
    assert _item(db, second, "Brustpresse")["defaults"]["weight"] == 25


def test_weight_follows_most_recent_session_also_downwards(db: GymDatabase) -> None:
    _workout(db, START, {"Beinpresse": 70})
    _workout(db, START + timedelta(days=2), {"Beinpresse": 66})
    third = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    assert _item(db, third, "Beinpresse")["defaults"]["weight"] == 66


def test_skipped_exercise_does_not_change_weight(db: GymDatabase) -> None:
    _workout(db, START, {"Beinpresse": 65})
    second = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    db.save_entry(second, _exercise(db, "Beinpresse")["id"], START + timedelta(days=2, minutes=5), skipped=True)
    plank = _exercise(db, "Plank")["id"]
    db.save_entry(second, plank, START + timedelta(days=2, minutes=9), sets=3, seconds=30, rpe=6)
    db.finish_session(second, START + timedelta(days=2, minutes=30))
    third = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    assert _item(db, third, "Beinpresse")["defaults"]["weight"] == 65


def test_cancelled_session_weight_counts(db: GymDatabase) -> None:
    first = _workout(db, START, {"Beinpresse": 67.5}, finish=False)
    assert db.cancel_session(first, START + timedelta(minutes=20))
    assert db.session_detail(first)["status"] == "cancelled"
    second = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    assert _item(db, second, "Beinpresse")["defaults"]["weight"] == 67.5


def test_weight_of_running_session_does_not_change_its_own_default(db: GymDatabase) -> None:
    _workout(db, START, {"Beinpresse": 65})
    second = _workout(db, START + timedelta(days=2), {"Beinpresse": 70}, finish=False)
    item = _item(db, second, "Beinpresse")
    assert item["defaults"]["weight"] == 65
    assert item["entry"]["weight"] == 70
    assert item["weight_change"] == {"from": 65, "to": 70, "delta": 5}


def test_editing_an_old_session_does_not_override_newer_weight(db: GymDatabase) -> None:
    first = _workout(db, START, {"Beinpresse": 65})
    _workout(db, START + timedelta(days=2), {"Beinpresse": 70})
    press = _exercise(db, "Beinpresse")["id"]
    db.save_entry(first, press, START + timedelta(days=5), sets=3, reps=14, weight=80, rpe=9)
    third = db.start_session(None, START + timedelta(days=7), date(2026, 11, 9))
    assert _item(db, third, "Beinpresse")["defaults"]["weight"] == 70


def test_deleting_last_session_falls_back(db: GymDatabase) -> None:
    _workout(db, START, {"Beinpresse": 65})
    second = _workout(db, START + timedelta(days=2), {"Beinpresse": 70})
    db.delete_session(second)
    third = db.start_session(None, START + timedelta(days=4), date(2026, 11, 6))
    assert _item(db, third, "Beinpresse")["defaults"]["weight"] == 65


def test_only_one_running_session(db: GymDatabase) -> None:
    first = db.start_session(None, START, START.date())
    assert db.start_session(None, START + timedelta(minutes=1), START.date()) == first
    assert db.running_session_id() == first


def test_resume_keeps_progress(db: GymDatabase) -> None:
    session_id = _workout(db, START, {"Beinpresse": 63}, finish=False)
    db.set_warmup_done(session_id, True)
    detail = db.session_detail(db.running_session_id())
    assert detail["warmup_done"] is True
    assert detail["status"] == "running"
    assert [bool(i["entry"]) for i in detail["items"]] == [True] + [False] * 9
    assert detail["summary"]["duration_s"] is None


def test_save_entry_is_idempotent_and_replaces(db: GymDatabase) -> None:
    session_id = db.start_session(None, START, START.date())
    press = _exercise(db, "Beinpresse")["id"]
    first = db.save_entry(session_id, press, START, sets=3, reps=14, weight=63, rpe=7)
    again = db.save_entry(session_id, press, START, sets=2, reps=12, weight=65, rpe=9, note="schwer")
    assert first == again
    entry = _item(db, session_id, "Beinpresse")["entry"]
    assert (entry["set_count"], entry["reps"], entry["weight"], entry["rpe"], entry["note"]) == (2, 12, 65, 9, "schwer")
    assert entry["volume"] == 2 * 12 * 65
    assert len(db.export_rows()) == 2


def test_skip_then_record(db: GymDatabase) -> None:
    session_id = db.start_session(None, START, START.date())
    press = _exercise(db, "Beinpresse")["id"]
    db.save_entry(session_id, press, START, skipped=True, rpe=5)
    entry = _item(db, session_id, "Beinpresse")["entry"]
    assert entry["skipped"] is True
    assert entry["rpe"] is None
    assert entry["sets"] == []
    db.save_entry(session_id, press, START, sets=3, reps=14, weight=63, rpe=7)
    assert _item(db, session_id, "Beinpresse")["entry"]["skipped"] is False


def test_entry_validation(db: GymDatabase) -> None:
    session_id = db.start_session(None, START, START.date())
    press = _exercise(db, "Beinpresse")["id"]
    plank = _exercise(db, "Plank")["id"]
    for kwargs in (
        {"sets": 0, "reps": 14, "weight": 63},
        {"sets": 3, "reps": None, "weight": 63},
        {"sets": 3, "reps": 14, "weight": None},
        {"sets": 3, "reps": 14, "weight": 63, "rpe": 11},
        {"sets": 3, "reps": 14, "weight": 63, "rpe": 0},
    ):
        with pytest.raises(GymError):
            db.save_entry(session_id, press, START, **kwargs)
    with pytest.raises(GymError):
        db.save_entry(session_id, plank, START, sets=3, reps=10)
    with pytest.raises(GymError):
        db.save_entry(session_id, 9999, START, sets=3, reps=10)
    with pytest.raises(GymError):
        db.save_entry(9999, press, START, sets=3, reps=10, weight=1)
    # A weight sent for an exercise without weight is ignored.
    db.save_entry(session_id, plank, START, sets=3, seconds=45, weight=20, rpe=6)
    entry = _item(db, session_id, "Plank")["entry"]
    assert (entry["seconds"], entry["weight"], entry["volume"]) == (45, None, 0)


def test_finish_and_summary(db: GymDatabase) -> None:
    session_id = _workout(db, START, {"Beinpresse": 65.5, "Plank": 0, "Bizeps Curls": 18})
    db.save_entry(session_id, _exercise(db, "Brustpresse")["id"], START, skipped=True)
    detail = db.session_detail(session_id)
    assert detail["status"] == "completed"
    summary = detail["summary"]
    assert summary["duration_s"] == 3600
    assert summary["volume"] == 3 * 14 * 65.5 + 2 * 15 * 18
    assert (summary["done"], summary["skipped"], summary["total"]) == (3, 1, 10)
    assert summary["avg_rpe"] == 7
    assert summary["weight_changes"] == [
        {"exercise_id": _exercise(db, "Beinpresse")["id"], "name": "Beinpresse", "from": 63, "to": 65.5, "delta": 2.5}
    ]
    with pytest.raises(GymError):
        db.finish_session(session_id, START)


def test_finish_requires_an_entry_and_cancel_removes_empty(db: GymDatabase) -> None:
    session_id = db.start_session(None, START, START.date())
    with pytest.raises(GymError) as err:
        db.finish_session(session_id, START)
    assert err.value.code == "empty_session"
    assert db.cancel_session(session_id, START) is False
    with pytest.raises(GymError):
        db.session_detail(session_id)
    assert db.running_session_id() is None


def test_forgotten_finish_ends_at_last_entry(db: GymDatabase) -> None:
    session_id = _workout(db, START, {"Beinpresse": 63}, finish=False)
    db.finish_session(session_id, START + timedelta(hours=20))
    assert db.session_detail(session_id)["summary"]["duration_s"] == 300


def test_delete_entry_and_session(db: GymDatabase) -> None:
    session_id = _workout(db, START, {"Beinpresse": 63, "Bizeps Curls": 18})
    entry_id = _item(db, session_id, "Beinpresse")["entry"]["id"]
    assert db.delete_entry(entry_id) == session_id
    assert _item(db, session_id, "Beinpresse")["entry"] is None
    assert len(db.export_rows()) == 2
    db.delete_session(session_id)
    assert db.export_rows() == []
    assert db.list_sessions() == []
    with pytest.raises(GymError):
        db.delete_entry(entry_id)


def test_summary_counts_completed_sessions_per_week_and_month(db: GymDatabase) -> None:
    _workout(db, datetime(2026, 10, 30, 17, tzinfo=UTC), {"Beinpresse": 63})  # previous month
    _workout(db, datetime(2026, 11, 3, 17, tzinfo=UTC), {"Beinpresse": 63})  # previous week
    _workout(db, datetime(2026, 11, 9, 17, tzinfo=UTC), {"Beinpresse": 63})  # Monday of this week
    _workout(db, datetime(2026, 11, 11, 17, tzinfo=UTC), {"Beinpresse": 63})
    cancelled = _workout(db, datetime(2026, 11, 12, 17, tzinfo=UTC), {"Beinpresse": 63}, finish=False)
    db.cancel_session(cancelled, datetime(2026, 11, 12, 18, tzinfo=UTC))
    _workout(db, datetime(2026, 11, 13, 17, tzinfo=UTC), {"Beinpresse": 63}, finish=False)  # still running
    summary = db.summary(date(2026, 11, 13))
    assert summary["week"] == 2
    assert summary["month"] == 3
    assert summary["total"] == 4
    assert summary["last_date"] == "2026-11-11"
    assert (summary["freq_min"], summary["freq_max"]) == (2, 3)
    # A new week starts on Monday.
    assert db.summary(date(2026, 11, 16))["week"] == 0
    assert db.summary(date(2026, 12, 1))["month"] == 0


def test_summary_without_data(db: GymDatabase) -> None:
    summary = db.summary(date(2026, 11, 13))
    assert (summary["last_date"], summary["week"], summary["month"]) == (None, 0, 0)


def test_list_sessions(db: GymDatabase) -> None:
    first = _workout(db, START, {"Beinpresse": 63}, rpe=6)
    second = _workout(db, START + timedelta(days=2), {"Beinpresse": 65, "Plank": 0}, rpe=8)
    _workout(db, START + timedelta(days=4), {"Beinpresse": 65}, finish=False)
    sessions = db.list_sessions()
    assert [s["id"] for s in sessions] == [second, first]
    assert sessions[0]["volume"] == 3 * 14 * 65
    assert (sessions[0]["done"], sessions[0]["avg_rpe"], sessions[0]["duration_s"]) == (2, 8, 3600)
    assert sessions[0]["plan_name"] == "Ganzkörper-Training"
    assert [s["id"] for s in db.list_sessions(limit=1, offset=1)] == [first]
    assert [s["id"] for s in db.list_sessions(since=date(2026, 11, 3))] == [second]


def test_exercise_stats_and_records(db: GymDatabase) -> None:
    press = _exercise(db, "Beinpresse")["id"]
    first = _workout(db, START, {"Beinpresse": 63})
    second = _workout(db, START + timedelta(days=7), {"Beinpresse": 68})
    third = _workout(db, START + timedelta(days=14), {"Beinpresse": 68})
    _workout(db, START + timedelta(days=15), {"Beinpresse": 100}, finish=False)  # running: not part of statistics
    stats = db.exercise_stats(press, None)
    assert [p["session_id"] for p in stats["points"]] == [first, second, third]
    assert [p["weight"] for p in stats["points"]] == [63, 68, 68]
    assert stats["points"][0]["volume"] == 3 * 14 * 63
    assert stats["records"]["weight"] == {"value": 68, "date": "2026-11-09", "session_id": second}
    assert stats["records"]["volume"]["session_id"] == second
    assert stats["points"][1]["records"] == ["weight", "volume"]
    assert stats["points"][2]["records"] == []
    # The range limits the points, records stay all-time.
    ranged = db.exercise_stats(press, date(2026, 11, 10))
    assert [p["session_id"] for p in ranged["points"]] == [third]
    assert ranged["records"]["weight"]["session_id"] == second
    with pytest.raises(GymError):
        db.exercise_stats(9999, None)


def test_exercise_stats_timed(db: GymDatabase) -> None:
    plank = _exercise(db, "Plank")["id"]
    session_id = db.start_session(None, START, START.date())
    db.save_entry(session_id, plank, START, sets=3, seconds=40, rpe=8)
    db.finish_session(session_id, START + timedelta(minutes=10))
    stats = db.exercise_stats(plank, None)
    assert stats["records"] == {"seconds": {"value": 40, "date": "2026-11-02", "session_id": session_id}}
    assert stats["points"][0]["weight"] is None


def test_overview_stats(db: GymDatabase) -> None:
    _workout(db, START, {"Beinpresse": 63}, rpe=6)
    _workout(db, START + timedelta(days=2), {"Beinpresse": 63}, rpe=8)
    _workout(db, START + timedelta(days=9), {"Beinpresse": 63}, rpe=8)
    today = date(2026, 11, 22)
    stats = db.overview_stats(date(2026, 11, 2), today)
    assert stats["granularity"] == "week"
    assert [b["count"] for b in stats["buckets"]] == [2, 1, 0]
    assert stats["totals"]["count"] == 3
    assert stats["totals"]["volume"] == 3 * 3 * 14 * 63
    assert stats["totals"]["avg_rpe"] == 7.33
    assert stats["totals"]["avg_duration_s"] == 3600
    assert stats["totals"]["per_week"] == 1.0
    assert (stats["freq_min"], stats["freq_max"]) == (2, 3)
    # "all" starts with the first session.
    assert db.overview_stats(None, today)["start"] == "2026-11-02"
    assert db.overview_stats(date(2026, 11, 20), today)["totals"]["count"] == 0


def test_export_csv(db: GymDatabase) -> None:
    session_id = _workout(db, START, {"Beinpresse": 62.5, "Plank": 0})
    db.save_entry(session_id, _exercise(db, "Brustpresse")["id"], START, skipped=True, note="Schulter")
    lines = db.export_csv().strip().split("\r\n")
    assert len(lines) == 1 + 3 + 3 + 1
    assert lines[1].split(";")[5:11] == ["Beinpresse", "Beine & Rumpf", "1", "14", "", "62,5"]
    assert lines[4].split(";")[5:11] == ["Plank", "Beine & Rumpf", "1", "", "30", ""]
    assert lines[7].split(";")[5:] == ["Brustpresse", "Oberkörper", "", "", "", "", "", "ja", "Schulter"]
