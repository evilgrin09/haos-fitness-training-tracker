"""Tests for editing the plan, spontaneous exercises and the schema migration."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from custom_components.gym_tracker.db import MIGRATIONS, GymDatabase, GymError

from .helpers import USER, UserDB

START = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)
WEIGHT = {"name": "Wadenheben", "kind": "weight", "target_sets": 3, "reps_min": 12, "reps_max": 14, "start_weight": 40}
REPS = {"name": "Klimmzüge", "kind": "reps", "target_sets": 3, "reps_min": 5, "reps_max": 8}
TIME = {"name": "Seitstütz", "kind": "time", "target_sets": 2, "duration_s": 45}


@pytest.fixture
def db(tmp_path: Path) -> UserDB:
    """Database with the default plan of one user."""
    database = GymDatabase(tmp_path / "gym.db")
    database.create_user_plan(USER, START, template=True)
    yield UserDB(database)
    database.close()


def _plan(db: UserDB) -> dict:
    return db.get_plans()[0]


def _names(db: UserDB) -> list[str]:
    return [e["name"] for e in _plan(db)["exercises"]]


def _by_name(db: UserDB, name: str) -> dict:
    plan = _plan(db)
    return next(e for e in plan["exercises"] + plan["archived_exercises"] if e["name"] == name)


def _assert_gapless(db: UserDB) -> None:
    exercises = _plan(db)["exercises"]
    assert [e["position"] for e in exercises] == list(range(1, len(exercises) + 1))
    groups = [e["muscle_group"] for e in exercises]
    seen = [g for i, g in enumerate(groups) if i == 0 or groups[i - 1] != g]
    assert len(seen) == len(set(seen)), "every group has to stay together"


def _record(db: UserDB, when: datetime, name: str, **values) -> int:
    session_id = db.start_session(None, when, when.date())
    db.save_entry(session_id, _by_name(db, name)["id"], when + timedelta(minutes=5), rpe=7, **values)
    db.finish_session(session_id, when + timedelta(minutes=30))
    return session_id


def _error(code: str):
    return pytest.raises(GymError, check=lambda err: err.code == code)


# ------------------------------------------------------------------- adding


def test_add_goes_to_end_of_its_group(db: UserDB) -> None:
    exercise_id = db.add_exercise(None, {**WEIGHT, "muscle_group": "Beine & Rumpf", "setting": " Stufe 3 "})
    names = _names(db)
    assert names.index("Wadenheben") == 4  # after Plank, before the first upper-body exercise
    assert names[5] == "Rudern (brustgestützt)"
    added = _by_name(db, "Wadenheben")
    assert added["id"] == exercise_id
    assert (added["kind"], added["weighted"], added["start_weight"]) == ("weight", True, 40)
    assert added["setting"] == "Stufe 3"
    assert (added["target_sets"], added["reps_min"], added["reps_max"], added["duration_s"]) == (3, 12, 14, None)
    assert (added["entry_count"], added["last_weight"]) == (0, None)
    _assert_gapless(db)


def test_add_without_group_goes_to_end_of_plan(db: UserDB) -> None:
    db.add_exercise(None, REPS)
    db.add_exercise(None, {**TIME, "muscle_group": "Mobilität"})
    names = _names(db)
    # A new group is appended, exercises without group always stay last.
    assert names[-2:] == ["Seitstütz", "Klimmzüge"]
    reps, timed = _by_name(db, "Klimmzüge"), _by_name(db, "Seitstütz")
    assert (reps["kind"], reps["weighted"], reps["start_weight"]) == ("reps", False, None)
    assert (timed["kind"], timed["duration_s"], timed["reps_min"], timed["reps_max"]) == ("time", 45, None, None)
    _assert_gapless(db)


def test_add_normalises_fields_of_other_kinds(db: UserDB) -> None:
    db.add_exercise(None, {**TIME, "reps_min": 5, "reps_max": 8, "start_weight": 20})
    timed = _by_name(db, "Seitstütz")
    assert (timed["reps_min"], timed["start_weight"], timed["weighted"]) == (None, None, False)


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"name": "   "}, "name_required"),
        ({"name": "beinpresse "}, "name_exists"),
        ({"kind": "other"}, "invalid_values"),
        ({"target_sets": 0}, "invalid_values"),
        ({"reps_min": 0}, "invalid_values"),
        ({"reps_min": 15, "reps_max": 14}, "invalid_values"),
        ({"start_weight": 0}, "invalid_values"),
        ({"start_weight": None}, "invalid_values"),
        ({"kind": "time", "duration_s": 0}, "invalid_values"),
        ({"kind": "time"}, "invalid_values"),
    ],
)
def test_add_validation(db: UserDB, change: dict, code: str) -> None:
    with _error(code):
        db.add_exercise(None, {**WEIGHT, **change})
    assert len(_names(db)) == 10


def test_single_rep_number_is_allowed(db: UserDB) -> None:
    db.add_exercise(None, {**REPS, "reps_min": 10, "reps_max": 10})
    assert _by_name(db, "Klimmzüge")["reps_min"] == 10


# ---------------------------------------------------- archiving / restoring


def test_archive_keeps_history_and_closes_gaps(db: UserDB) -> None:
    session_id = _record(db, START, "Beinbeuger (liegend)", sets=3, reps=14, weight=30)
    curl = _by_name(db, "Beinbeuger (liegend)")
    db.archive_exercise(curl["id"])
    plan = _plan(db)
    assert "Beinbeuger (liegend)" not in _names(db)
    assert [e["name"] for e in plan["archived_exercises"]] == ["Beinbeuger (liegend)"]
    assert plan["archived_exercises"][0]["entry_count"] == 1
    _assert_gapless(db)
    # Not offered in the next training any more ...
    new_session = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    assert "Beinbeuger (liegend)" not in [i["exercise"]["name"] for i in db.session_detail(new_session)["items"]]
    # ... but history, statistics and export keep everything.
    old = db.session_detail(session_id)
    item = next(i for i in old["items"] if i["exercise"]["id"] == curl["id"])
    assert item["exercise"]["archived"] is True
    assert item["entry"]["weight"] == 30
    stats = db.exercise_stats(curl["id"], None)
    assert [p["weight"] for p in stats["points"]] == [30]
    assert stats["exercise"]["archived"] is True
    row = db.export_rows()[0]
    assert (row["exercise"], row["archived"]) == ("Beinbeuger (liegend)", 1)
    assert db.export_csv().split("\r\n")[1].endswith(";ja")
    assert db.summary(date(2026, 11, 2))["total"] == 1


def test_restore_goes_to_end_of_group_with_values_and_history(db: UserDB) -> None:
    _record(db, START, "Beinpresse", sets=3, reps=14, weight=70)
    press = _by_name(db, "Beinpresse")
    db.archive_exercise(press["id"])
    db.restore_exercise(press["id"])
    names = _names(db)
    assert names[:4] == ["Beinbeuger (liegend)", "Rückenstrecker", "Plank", "Beinpresse"]
    restored = _by_name(db, "Beinpresse")
    assert (restored["id"], restored["archived"], restored["setting"]) == (press["id"], False, "Sitz 6–7")
    assert (restored["entry_count"], restored["last_weight"], restored["last_date"]) == (1, 70, "2026-11-02")
    _assert_gapless(db)
    # The weight recorded before archiving still is the default.
    session_id = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    item = next(i for i in db.session_detail(session_id)["items"] if i["exercise"]["id"] == press["id"])
    assert item["defaults"]["weight"] == 70


def test_name_of_archived_exercise_suggests_restore_instead_of_duplicate(db: UserDB) -> None:
    press = _by_name(db, "Beinpresse")
    db.archive_exercise(press["id"])
    with _error("name_archived"):
        db.add_exercise(None, {**WEIGHT, "name": "BEINPRESSE"})
    # The panel offers the archived exercise while typing and restores it.
    suggestions = [e for e in _plan(db)["archived_exercises"] if "beinpr" in e["name"].casefold()]
    db.restore_exercise(suggestions[0]["id"])
    assert _names(db).count("Beinpresse") == 1
    assert _plan(db)["archived_exercises"] == []


def test_restore_is_blocked_by_active_exercise_of_same_name(db: UserDB) -> None:
    press = _by_name(db, "Beinpresse")
    db.archive_exercise(press["id"])
    db.update_exercise(_by_name(db, "Plank")["id"], {**TIME, "name": "Plank lang", "muscle_group": "Beine & Rumpf"})
    with _error("name_archived"):
        db.update_exercise(_by_name(db, "Plank lang")["id"], {**TIME, "name": "Beinpresse"})
    connection = sqlite3.connect(db._conn.execute("PRAGMA database_list").fetchone()[2])
    connection.execute("UPDATE exercises SET name = 'Beinpresse' WHERE name = 'Plank lang'")
    connection.commit()
    connection.close()
    with _error("name_exists"):
        db.restore_exercise(press["id"])


# ------------------------------------------------------------------ editing


def test_update_changes_values_but_not_past_entries(db: UserDB) -> None:
    session_id = _record(db, START, "Beinpresse", sets=3, reps=14, weight=70)
    press = _by_name(db, "Beinpresse")
    db.update_exercise(
        press["id"],
        {**WEIGHT, "name": "Beinpresse 45°", "target_sets": 4, "reps_min": 8, "reps_max": 10, "start_weight": 100,
         "muscle_group": "Beine & Rumpf", "hint": "Tief gehen"},
    )  # fmt: skip
    updated = _by_name(db, "Beinpresse 45°")
    assert (updated["id"], updated["position"]) == (press["id"], 1)
    assert (updated["target_sets"], updated["reps_min"], updated["reps_max"]) == (4, 8, 10)
    assert (updated["start_weight"], updated["hint"], updated["setting"]) == (100, "Tief gehen", "")
    entry = db.session_detail(session_id)["items"][0]["entry"]
    assert (entry["set_count"], entry["reps"], entry["weight"]) == (3, 14, 70)
    # The weight recorded last still wins over the new start weight.
    new_session = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    defaults = db.session_detail(new_session)["items"][0]["defaults"]
    assert defaults == {"sets": 4, "reps": 10, "seconds": None, "weight": 70}


def test_new_start_weight_applies_while_there_are_no_entries(db: UserDB) -> None:
    press = _by_name(db, "Beinpresse")
    data = {**WEIGHT, "name": "Beinpresse", "start_weight": 80, "muscle_group": "Beine & Rumpf"}
    db.update_exercise(press["id"], data)
    session_id = db.start_session(None, START, START.date())
    assert db.session_detail(session_id)["items"][0]["defaults"]["weight"] == 80


def test_kind_is_locked_once_entries_exist(db: UserDB) -> None:
    plank = _by_name(db, "Plank")
    as_reps = {**REPS, "name": "Plank", "muscle_group": "Beine & Rumpf"}
    # Without entries the kind may change (a skipped entry does not count).
    session_id = db.start_session(None, START, START.date())
    db.save_entry(session_id, plank["id"], START, skipped=True)
    db.cancel_session(session_id, START)
    db.update_exercise(plank["id"], as_reps)
    assert _by_name(db, "Plank")["kind"] == "reps"
    _record(db, START + timedelta(days=1), "Plank", sets=3, reps=8)
    with _error("kind_locked"):
        db.update_exercise(plank["id"], {**TIME, "name": "Plank", "muscle_group": "Beine & Rumpf"})
    with _error("kind_locked"):
        db.update_exercise(plank["id"], {**WEIGHT, "name": "Plank", "muscle_group": "Beine & Rumpf"})
    # Everything else stays editable.
    db.update_exercise(plank["id"], {**as_reps, "target_sets": 4})
    assert _by_name(db, "Plank")["target_sets"] == 4


def test_update_validation_and_group_change(db: UserDB) -> None:
    plank = _by_name(db, "Plank")
    with _error("name_exists"):
        db.update_exercise(plank["id"], {**TIME, "name": "Beinpresse"})
    with _error("invalid_values"):
        db.update_exercise(plank["id"], {**TIME, "name": "Plank", "duration_s": 0})
    with _error("not_found"):
        db.update_exercise(9999, TIME)
    # Keeping the own name is fine; a new group moves the exercise to the end of that group.
    db.update_exercise(plank["id"], {**TIME, "name": "Plank", "muscle_group": "Arme"})
    assert _names(db)[-1] == "Plank"
    _assert_gapless(db)
    db.archive_exercise(plank["id"])
    with _error("archived"):
        db.update_exercise(plank["id"], {**TIME, "name": "Plank"})


# ------------------------------------------------------------------- moving


def test_move_within_group(db: UserDB) -> None:
    press, curl = _by_name(db, "Beinpresse"), _by_name(db, "Beinbeuger (liegend)")
    db.move_exercise(press["id"], 1)
    assert _names(db)[:3] == ["Beinbeuger (liegend)", "Beinpresse", "Rückenstrecker"]
    db.move_exercise(press["id"], -1)
    assert _names(db)[:2] == ["Beinpresse", "Beinbeuger (liegend)"]
    _assert_gapless(db)
    with _error("edge"):
        db.move_exercise(press["id"], -1)
    # The last exercise of a group does not cross into the next group.
    plank = _by_name(db, "Plank")
    with _error("edge"):
        db.move_exercise(plank["id"], 1)
    db.archive_exercise(curl["id"])
    with _error("archived"):
        db.move_exercise(curl["id"], 1)


def test_move_group(db: UserDB) -> None:
    db.add_exercise(None, REPS)  # without group, always last
    db.move_group(None, "Arme", -1)
    groups = [e["muscle_group"] for e in _plan(db)["exercises"]]
    assert groups == ["Beine & Rumpf"] * 4 + ["Arme"] * 2 + ["Oberkörper"] * 4 + [""]
    _assert_gapless(db)
    db.move_group(None, "Beine & Rumpf", 1)
    assert _names(db)[:2] == ["Bizeps Curls", "Trizeps Extensions"]
    with _error("edge"):
        db.move_group(None, "Arme", -1)
    with _error("edge"):
        db.move_group(None, "Oberkörper", 1)  # would pass the exercises without group
    with _error("edge"):
        db.move_group(None, "", -1)
    with _error("not_found"):
        db.move_group(None, "Gibt es nicht", 1)


def test_plan_is_locked_while_a_session_is_running(db: UserDB) -> None:
    press = _by_name(db, "Beinpresse")
    db.archive_exercise(_by_name(db, "Plank")["id"])
    session_id = db.start_session(None, START, START.date())
    for change in (
        lambda: db.add_exercise(None, WEIGHT),
        lambda: db.update_exercise(press["id"], {**WEIGHT, "name": "Beinpresse"}),
        lambda: db.move_exercise(press["id"], 1),
        lambda: db.move_group(None, "Arme", -1),
        lambda: db.archive_exercise(press["id"]),
        lambda: db.restore_exercise(_by_name(db, "Plank")["id"]),
    ):
        with _error("session_running"):
            change()
    db.cancel_session(session_id, START)
    db.move_exercise(press["id"], 1)


# ------------------------------------------------------ spontaneous exercise


def _spontaneous(db: UserDB, data: dict = WEIGHT) -> tuple[int, int]:
    session_id = db.start_session(None, START, START.date())
    db.save_entry(session_id, _by_name(db, "Beinpresse")["id"], START, sets=3, reps=14, weight=63, rpe=7)
    exercise_id = db.add_session_exercise(session_id, {**data, "muscle_group": "Beine & Rumpf"})
    return session_id, exercise_id


def test_spontaneous_exercise_is_part_of_the_session_only(db: UserDB) -> None:
    session_id, exercise_id = _spontaneous(db)
    detail = db.session_detail(session_id)
    assert len(detail["items"]) == 11
    item = detail["items"][-1]
    assert (item["exercise"]["id"], item["adhoc"], item["entry"]) == (exercise_id, True, None)
    assert item["defaults"] == {"sets": 3, "reps": 14, "seconds": None, "weight": 40}
    assert [i["adhoc"] for i in detail["items"][:-1]] == [False] * 10
    # Not part of the plan yet.
    assert "Wadenheben" not in _names(db)
    with _error("name_exists"):
        db.add_session_exercise(session_id, {**WEIGHT, "name": "Brustpresse"})
    with _error("name_archived"):
        db.add_session_exercise(session_id, WEIGHT)
    with _error("invalid_values"):
        db.add_session_exercise(session_id, {**WEIGHT, "name": "Neu", "target_sets": 0})


def test_spontaneous_exercise_adopted_into_plan(db: UserDB) -> None:
    session_id, exercise_id = _spontaneous(db)
    db.save_entry(session_id, exercise_id, START + timedelta(minutes=10), sets=3, reps=12, weight=42.5, rpe=8)
    db.finish_session(session_id, START + timedelta(minutes=30), adopt=[exercise_id])
    names = _names(db)
    assert names.index("Wadenheben") == 4  # end of its group
    _assert_gapless(db)
    detail = db.session_detail(session_id)
    item = detail["items"][-1]
    assert (item["exercise"]["name"], item["adhoc"], item["exercise"]["archived"]) == ("Wadenheben", True, False)
    assert item["weight_change"] == {"from": 40, "to": 42.5, "delta": 2.5}
    assert detail["summary"]["done"] == 2
    assert detail["summary"]["volume"] == 3 * 14 * 63 + 3 * 12 * 42.5
    # From the next training on it is a normal plan exercise with the recorded weight.
    next_id = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    item = next(i for i in db.session_detail(next_id)["items"] if i["exercise"]["id"] == exercise_id)
    assert (item["adhoc"], item["defaults"]["weight"]) == (False, 42.5)


def test_spontaneous_exercise_declined_stays_in_history(db: UserDB) -> None:
    session_id, exercise_id = _spontaneous(db, TIME)
    db.save_entry(session_id, exercise_id, START + timedelta(minutes=10), sets=2, seconds=50, rpe=6)
    db.finish_session(session_id, START + timedelta(minutes=30))
    assert "Seitstütz" not in _names(db)
    assert [e["name"] for e in _plan(db)["archived_exercises"]] == ["Seitstütz"]
    _assert_gapless(db)
    item = db.session_detail(session_id)["items"][-1]
    assert (item["adhoc"], item["exercise"]["archived"], item["entry"]["seconds"]) == (True, True, 50)
    assert db.exercise_stats(exercise_id, None)["records"]["seconds"]["value"] == 50
    assert "Seitstütz" in db.export_csv()
    next_id = db.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    assert len(db.session_detail(next_id)["items"]) == 10


def test_archived_exercise_can_be_done_spontaneously(db: UserDB) -> None:
    _record(db, START - timedelta(days=7), "Plank", sets=3, seconds=40)
    plank = _by_name(db, "Plank")
    db.archive_exercise(plank["id"])
    session_id = db.start_session(None, START, START.date())
    assert db.add_session_exercise(session_id, exercise_id=plank["id"]) == plank["id"]
    assert db.add_session_exercise(session_id, exercise_id=plank["id"]) == plank["id"]  # idempotent
    item = db.session_detail(session_id)["items"][-1]
    assert (item["exercise"]["name"], item["adhoc"], item["previous"]["seconds"]) == ("Plank", True, 40)
    with _error("name_exists"):
        db.add_session_exercise(session_id, exercise_id=_by_name(db, "Beinpresse")["id"])
    db.save_entry(session_id, plank["id"], START, sets=3, seconds=45, rpe=7)
    db.finish_session(session_id, START + timedelta(minutes=20), adopt=[plank["id"]])
    assert _names(db)[3] == "Plank"
    assert _by_name(db, "Plank")["entry_count"] == 2


def test_cancelled_session_archives_spontaneous_exercise(db: UserDB) -> None:
    session_id, exercise_id = _spontaneous(db)
    db.save_entry(session_id, exercise_id, START, sets=3, reps=12, weight=40, rpe=7)
    assert db.cancel_session(session_id, START + timedelta(minutes=5))
    assert "Wadenheben" not in _names(db)
    assert _by_name(db, "Wadenheben")["entry_count"] == 1
    assert db.session_detail(session_id)["items"][-1]["adhoc"] is True


def test_discarded_session_removes_unused_spontaneous_exercise(db: UserDB) -> None:
    session_id = db.start_session(None, START, START.date())
    db.add_session_exercise(session_id, WEIGHT)
    assert db.cancel_session(session_id, START) is False
    assert _plan(db)["archived_exercises"] == []
    db.add_exercise(None, WEIGHT)  # the name is free again
    # An archived exercise that was merely reused is never deleted.
    db.archive_exercise(_by_name(db, "Wadenheben")["id"])
    session_id = db.start_session(None, START, START.date())
    db.add_session_exercise(session_id, exercise_id=_by_name(db, "Wadenheben")["id"])
    db.cancel_session(session_id, START)
    assert [e["name"] for e in _plan(db)["archived_exercises"]] == ["Wadenheben"]


def test_finish_removes_new_spontaneous_exercise_without_entry(db: UserDB) -> None:
    session_id, unused = _spontaneous(db)  # Wadenheben: added, never recorded
    recorded = db.add_session_exercise(session_id, TIME)
    db.save_entry(session_id, recorded, START + timedelta(minutes=10), sets=2, seconds=45, rpe=6)
    db.finish_session(session_id, START + timedelta(minutes=30))
    # The untouched one is gone, the recorded one stays archived with its entry.
    assert [e["name"] for e in _plan(db)["archived_exercises"]] == ["Seitstütz"]
    assert "Wadenheben" not in _names(db)
    items = db.session_detail(session_id)["items"]
    assert [i["exercise"]["name"] for i in items if i["adhoc"]] == ["Seitstütz"]
    assert len(items) == 11
    with _error("not_found"):
        db.exercise_stats(unused, None)
    db.add_exercise(None, WEIGHT)  # the name is free again
    _assert_gapless(db)


def test_finish_keeps_adopted_and_reused_exercises_without_entry(db: UserDB) -> None:
    db.archive_exercise(_by_name(db, "Plank")["id"])
    session_id, adopted = _spontaneous(db)
    plank = db.add_session_exercise(session_id, exercise_id=_by_name(db, "Plank")["id"])
    db.finish_session(session_id, START + timedelta(minutes=30), adopt=[adopted])
    # Adopted explicitly: becomes a plan exercise even without an entry.
    assert "Wadenheben" in _names(db)
    # An exercise taken from the archive existed before and is never deleted.
    assert [e["id"] for e in _plan(db)["archived_exercises"]] == [plank]


def test_cancel_removes_new_spontaneous_exercise_without_entry(db: UserDB) -> None:
    session_id, unused = _spontaneous(db)  # session has an entry for Beinpresse, so it is kept as cancelled
    skipped = db.add_session_exercise(session_id, TIME)
    db.save_entry(session_id, skipped, START + timedelta(minutes=5), skipped=True)
    assert db.cancel_session(session_id, START + timedelta(minutes=10)) is True
    # A skipped entry still is an entry (same rule as when deleting a session).
    assert [e["name"] for e in _plan(db)["archived_exercises"]] == ["Seitstütz"]
    assert [i["exercise"]["name"] for i in db.session_detail(session_id)["items"] if i["adhoc"]] == ["Seitstütz"]
    with _error("not_found"):
        db.exercise_stats(unused, None)


def test_spontaneous_exercise_needs_running_session(db: UserDB) -> None:
    session_id = _record(db, START, "Beinpresse", sets=3, reps=14, weight=63)
    with _error("not_running"):
        db.add_session_exercise(session_id, WEIGHT)


def test_plan_changes_do_not_alter_statistics(db: UserDB) -> None:
    _record(db, START, "Beinpresse", sets=3, reps=14, weight=63)
    _record(db, START + timedelta(days=2), "Plank", sets=3, seconds=30)
    today = date(2026, 11, 8)
    before = (db.summary(today), db.overview_stats(None, today), db.list_sessions())
    db.archive_exercise(_by_name(db, "Plank")["id"])
    db.add_exercise(None, WEIGHT)
    db.move_group(None, "Arme", -1)
    db.update_exercise(_by_name(db, "Beinpresse")["id"], {**WEIGHT, "name": "Presse", "start_weight": 10})
    assert (db.summary(today), db.overview_stats(None, today), db.list_sessions()) == before


# ---------------------------------------------------------------- migration


def test_migration_of_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(MIGRATIONS[0])
    old.executescript(
        """
        INSERT INTO plans (id, name, freq_min, freq_max, warmup, created_at)
            VALUES (1, 'Alt', 2, 3, 'Laufen', '2026-01-01');
        INSERT INTO exercises (id, plan_id, position, name, muscle_group, target_sets, reps_min, reps_max, weighted,
            start_weight, archived) VALUES (1, 1, 1, 'Kniebeuge', 'Beine', 3, 8, 10, 1, 60, 0),
            (2, 1, 2, 'Alt-Übung', 'Beine', 3, 8, 10, 0, NULL, 1);
        INSERT INTO sessions (id, uuid, plan_id, started_at, ended_at, local_date, status)
            VALUES (1, 'a', 1, '2026-10-01T17:00:00+00:00', '2026-10-01T18:00:00+00:00', '2026-10-01', 'completed');
        INSERT INTO entries (id, uuid, session_id, exercise_id, rpe, created_at)
            VALUES (1, 'b', 1, 1, 8, '2026-10-01T17:10:00+00:00');
        INSERT INTO entry_sets (entry_id, set_no, reps, weight) VALUES (1, 1, 10, 62.5), (1, 2, 10, 62.5);
        PRAGMA user_version = 1;
        """
    )
    old.close()

    raw = GymDatabase(path)
    assert raw._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert raw.backup_path == Path(f"{path}.bak-0.1.0")
    assert raw.unassigned() == {"plans": 1, "sessions": 1}
    raw.assign_unassigned(USER)
    db = UserDB(raw)
    plan = db.get_plans()[0]
    assert [e["name"] for e in plan["exercises"]] == ["Kniebeuge"]
    assert [e["name"] for e in plan["archived_exercises"]] == ["Alt-Übung"]
    detail = db.session_detail(1)
    assert detail["summary"]["volume"] == 2 * 10 * 62.5
    assert (detail["items"][0]["entry"]["weight"], detail["items"][0]["adhoc"]) == (62.5, False)
    assert db.summary(date(2026, 10, 2))["total"] == 1
    # New features work on the migrated data.
    session_id = db.start_session(None, START, START.date())
    assert db.session_detail(session_id)["items"][0]["defaults"]["weight"] == 62.5
    extra = db.add_session_exercise(session_id, {**TIME, "muscle_group": "Beine"})
    db.save_entry(session_id, extra, START, sets=2, seconds=45, rpe=6)
    db.finish_session(session_id, START + timedelta(minutes=10), adopt=[extra])
    assert [e["name"] for e in db.get_plans()[0]["exercises"]] == ["Kniebeuge", "Seitstütz"]
    db.close()
    # Opening again does not migrate twice.
    again = GymDatabase(path)
    assert again.backup_path is None
    again.close()
