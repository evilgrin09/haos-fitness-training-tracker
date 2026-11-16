"""Tests for the pure training logic."""

from datetime import UTC, date, datetime, timedelta

from custom_components.gym_tracker import logic

BASE = {"target_sets": 3, "reps_min": 12, "reps_max": 14, "duration_s": None}
WEIGHTED = {**BASE, "weighted": True, "start_weight": 63.0}
BODYWEIGHT = {**BASE, "weighted": False, "start_weight": None}
TIMED = {**BODYWEIGHT, "reps_min": None, "reps_max": None, "duration_s": 30}


def test_defaults_first_training_use_plan_weight() -> None:
    assert logic.next_defaults(WEIGHTED, None) == {"sets": 3, "reps": 14, "seconds": None, "weight": 63.0}


def test_defaults_use_last_recorded_weight() -> None:
    previous = {"weight": 65.5, "reps": 12, "set_count": 2}
    defaults = logic.next_defaults(WEIGHTED, previous)
    # Weight follows the last entry, sets and reps stay at the plan target.
    assert defaults == {"sets": 3, "reps": 14, "seconds": None, "weight": 65.5}


def test_defaults_lower_weight_is_taken_over_too() -> None:
    assert logic.next_defaults(WEIGHTED, {"weight": 60.0})["weight"] == 60.0


def test_defaults_without_weight() -> None:
    assert logic.next_defaults(BODYWEIGHT, {"weight": None})["weight"] is None
    assert logic.next_defaults(TIMED, None) == {"sets": 3, "reps": None, "seconds": 30, "weight": None}


def test_weight_change() -> None:
    assert logic.weight_change(WEIGHTED, None, {"skipped": False, "weight": 63.0}) is None
    assert logic.weight_change(WEIGHTED, None, {"skipped": False, "weight": 65.5}) == {
        "from": 63.0,
        "to": 65.5,
        "delta": 2.5,
    }
    assert logic.weight_change(WEIGHTED, {"weight": 65.5}, {"skipped": False, "weight": 63.0})["delta"] == -2.5
    assert logic.weight_change(WEIGHTED, None, {"skipped": True, "weight": None}) is None
    assert logic.weight_change(WEIGHTED, None, None) is None


def test_summarize_sets() -> None:
    sets = [
        {"reps": 14, "seconds": None, "weight": 63.0},
        {"reps": 14, "seconds": None, "weight": 63.0},
        {"reps": 12, "seconds": None, "weight": 63.0},
    ]
    summary = logic.summarize_sets(sets)
    assert summary["set_count"] == 3
    assert summary["reps"] == 14
    assert summary["max_reps"] == 14
    assert summary["avg_reps"] == 13.33
    assert summary["weight"] == 63.0
    assert summary["volume"] == 40 * 63.0


def test_summarize_sets_timed_and_empty() -> None:
    summary = logic.summarize_sets([{"reps": None, "seconds": 30, "weight": None}] * 3)
    assert (summary["seconds"], summary["volume"], summary["weight"]) == (30, 0, None)
    assert logic.summarize_sets([])["set_count"] == 0


def test_find_records_first_occurrence_wins() -> None:
    points = [
        {"session_id": 1, "date": "2026-01-01", "weight": 60, "volume": 2000, "max_reps": 14, "max_seconds": None},
        {"session_id": 2, "date": "2026-01-08", "weight": 65, "volume": 1900, "max_reps": 12, "max_seconds": None},
        {"session_id": 3, "date": "2026-01-15", "weight": 65, "volume": 2000, "max_reps": 14, "max_seconds": None},
    ]
    records = logic.find_records(points)
    assert records["weight"] == {"value": 65, "date": "2026-01-08", "session_id": 2}
    assert records["volume"]["session_id"] == 1
    assert records["reps"]["session_id"] == 1
    assert "seconds" not in records


def test_range_and_calendar_helpers() -> None:
    today = date(2026, 11, 16)  # a Monday
    assert logic.range_start("4w", today) == date(2026, 10, 20)
    assert logic.range_start("all", today) is None
    assert logic.week_start(date(2026, 11, 22)) == today
    assert logic.week_start(today) == today
    assert logic.month_start(today) == date(2026, 11, 1)


def test_bucketize_weeks_include_empty_ones() -> None:
    sessions = [
        {"date": "2026-11-02", "volume": 1000, "avg_rpe": 7},
        {"date": "2026-11-05", "volume": 500, "avg_rpe": 8},
        {"date": "2026-11-16", "volume": 800, "avg_rpe": None},
    ]
    buckets = logic.bucketize(sessions, date(2026, 11, 3), date(2026, 11, 16), "week")
    assert [b["start"] for b in buckets] == ["2026-11-02", "2026-11-09", "2026-11-16"]
    assert [b["count"] for b in buckets] == [2, 0, 1]
    assert buckets[0]["volume"] == 1500
    assert buckets[0]["avg_rpe"] == 7.5
    assert buckets[1]["avg_rpe"] is None
    assert buckets[2]["avg_rpe"] is None


def test_bucketize_months_across_year_end() -> None:
    sessions = [{"date": "2027-01-31", "volume": 1, "avg_rpe": 5}]
    buckets = logic.bucketize(sessions, date(2026, 11, 20), date(2027, 1, 31), "month")
    assert [b["start"] for b in buckets] == ["2026-11-01", "2026-12-01", "2027-01-01"]
    assert buckets[1]["end"] == "2026-12-31"
    assert [b["count"] for b in buckets] == [0, 0, 1]


def test_granularity() -> None:
    assert logic.choose_granularity(date(2026, 5, 18), date(2026, 11, 16)) == "week"
    assert logic.choose_granularity(date(2025, 11, 17), date(2026, 11, 16)) == "month"


def test_session_end() -> None:
    now = datetime(2026, 11, 16, 20, 0, tzinfo=UTC)
    assert logic.session_end(now, now - timedelta(minutes=5)) == now
    # Forgot to finish: the last recorded exercise marks the end.
    assert logic.session_end(now, now - timedelta(hours=5)) == now - timedelta(hours=5)
    assert logic.session_end(now, None) == now


def test_build_csv() -> None:
    row = {
        "date": "2026-11-16", "started_at": "2026-11-16T17:00:00+00:00", "ended_at": None, "status": "completed",
        "plan": "Ganzkörper-Training", "exercise": "Beinpresse", "muscle_group": "Beine & Rumpf", "set_no": 1,
        "reps": 14, "seconds": None, "weight": 62.5, "rpe": 7, "skipped": 0, "note": "gut; leicht",
    }  # fmt: skip
    text = logic.build_csv([row, {**row, "set_no": None, "reps": None, "weight": None, "rpe": None, "skipped": 1}])
    lines = text.split("\r\n")
    assert lines[0].startswith("﻿Datum;Start;Ende;Status;Plan;Übung")
    assert lines[1] == (
        '2026-11-16;2026-11-16T17:00:00+00:00;;abgeschlossen;Ganzkörper-Training;Beinpresse;'
        'Beine & Rumpf;1;14;;62,5;7;nein;"gut; leicht";nein'
    )
    assert lines[2].endswith(';;;;;;ja;"gut; leicht";nein')
    assert logic.build_csv([{**row, "archived": 1}]).split("\r\n")[1].endswith(";ja")
