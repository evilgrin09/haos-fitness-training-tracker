"""Tests for the separation of users and the migration of data from before 0.3.0."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import sqlite3

import pytest

from custom_components.gym_tracker import logic
from custom_components.gym_tracker.db import MIGRATIONS, GymDatabase, GymError

from .helpers import OTHER, USER, UserDB, make_v021_database, table_counts

START = datetime(2026, 11, 2, 17, 0, tzinfo=UTC)
WEIGHT = {"name": "Wadenheben", "kind": "weight", "target_sets": 3, "reps_min": 12, "reps_max": 14, "start_weight": 40}


def _error(code: str):
    return pytest.raises(GymError, check=lambda err: err.code == code)


@pytest.fixture
def raw(tmp_path: Path) -> GymDatabase:
    """Empty database."""
    database = GymDatabase(tmp_path / "gym.db")
    yield database
    database.close()


# --------------------------------------------------------------- migration


def test_migration_from_021_keeps_everything_and_makes_backup(tmp_path: Path) -> None:
    path = tmp_path / "gym_tracker.db"
    before = make_v021_database(path)
    assert before["sessions"] == 8
    assert before["entry_sets"] > 100

    database = GymDatabase(path)
    backup = Path(f"{path}.bak-0.2.1")
    assert database.backup_path == backup
    assert backup.exists()
    # The backup is a complete copy of the old database.
    assert table_counts(backup) == before
    assert sqlite3.connect(backup).execute("PRAGMA user_version").fetchone()[0] == 2
    assert database._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)

    # Nothing belongs to anybody yet, so nobody sees anything.
    assert database.unassigned() == {"plans": 1, "sessions": 8}
    assert database.get_plans(USER) == []
    assert database.plan_users() == []

    assert database.assign_unassigned(USER) == {"plans": 1, "sessions": 8}
    assert database.unassigned() == {"plans": 0, "sessions": 0}
    database.close()
    assert table_counts(path) == before

    db = UserDB(GymDatabase(path))
    assert db.backup_path is None  # already migrated
    (plan,) = db.get_plans()
    assert len(plan["exercises"]) == 10
    assert [e["name"] for e in plan["archived_exercises"]] == ["Klimmzüge"]
    sessions = db.list_sessions()
    assert len(sessions) == 8
    assert [s["status"] for s in sessions].count("cancelled") == 1
    detail = db.session_detail(2)
    assert detail["items"][-1]["adhoc"] is True
    assert detail["items"][0]["entry"]["weight"] == 65
    assert db.summary(date(2026, 10, 31))["total"] == 7
    assert db.raw.plan_users() == [USER]
    assert {row[0] for row in db.raw._conn.execute("SELECT DISTINCT user_id FROM sessions")} == {USER}
    # The weight recorded last is still the default of the next training.
    session_id = db.start_session(None, START, START.date())
    assert db.session_detail(session_id)["items"][0]["defaults"]["weight"] == 63 + 8
    db.close()


def test_backup_never_overwrites_an_older_one(tmp_path: Path) -> None:
    path = tmp_path / "gym_tracker.db"
    make_v021_database(path)
    Path(f"{path}.bak-0.2.1").write_text("older backup")
    database = GymDatabase(path)
    assert database.backup_path == Path(f"{path}.bak-0.2.1-2")
    assert Path(f"{path}.bak-0.2.1").read_text() == "older backup"
    database.close()


def test_new_database_needs_no_backup(raw: GymDatabase, tmp_path: Path) -> None:
    assert raw.backup_path is None
    assert list(tmp_path.glob("*.bak-*")) == []
    assert raw.unassigned() == {"plans": 0, "sessions": 0}


def test_assign_refuses_user_with_own_plan(tmp_path: Path) -> None:
    path = tmp_path / "gym_tracker.db"
    make_v021_database(path)
    database = GymDatabase(path)
    database.create_user_plan(OTHER, START, template=True)
    with _error("plan_exists"):
        database.assign_unassigned(OTHER)
    assert database.unassigned()["plans"] == 1
    database.close()


@pytest.mark.parametrize(
    ("users", "expected"),
    [
        ([{"id": "1", "name": "tim-mobile", "usernames": []}], "1"),
        ([{"id": "1", "name": "Tim", "usernames": ["Tim-Mobile"]}], "1"),
        ([{"id": "1", "name": "Tim", "usernames": ["tim"]}], None),
        ([], None),
        # Two candidates: nothing is guessed.
        (
            [{"id": "1", "name": "tim-mobile", "usernames": []}, {"id": "2", "name": "x", "usernames": ["tim-mobile"]}],
            None,
        ),
        # Display name and user name of the same user count once.
        ([{"id": "1", "name": "tim-mobile", "usernames": ["tim-mobile"]}], "1"),
        ([{"id": "1", "name": None, "usernames": []}], None),
    ],
)
def test_find_legacy_owner(users: list, expected: str | None) -> None:
    assert logic.find_legacy_owner(users, "tim-mobile") == expected


# --------------------------------------------------------------- plans


def test_create_plans(raw: GymDatabase) -> None:
    raw.create_user_plan(USER, START, template=True)
    raw.create_user_plan(OTHER, START, name="  Mein   Plan ", freq_min=1, freq_max=2)
    (template,) = raw.get_plans(USER)
    (empty,) = raw.get_plans(OTHER)
    assert (template["name"], len(template["exercises"])) == ("Ganzkörper-Training", 10)
    assert (empty["name"], empty["freq_min"], empty["freq_max"], empty["warmup"]) == ("Mein Plan", 1, 2, "")
    assert empty["exercises"] == []
    assert sorted(raw.plan_users()) == sorted([USER, OTHER])
    with _error("plan_exists"):
        raw.create_user_plan(OTHER, START, template=True)


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"name": " ", "freq_min": 1, "freq_max": 2}, "name_required"),
        ({"name": "Plan", "freq_min": 0, "freq_max": 2}, "invalid_values"),
        ({"name": "Plan", "freq_min": 3, "freq_max": 2}, "invalid_values"),
        ({"name": "Plan", "freq_min": 1, "freq_max": None}, "invalid_values"),
    ],
)
def test_create_empty_plan_validation(raw: GymDatabase, kwargs: dict, code: str) -> None:
    with _error(code):
        raw.create_user_plan(USER, START, **kwargs)
    assert raw.get_plans(USER) == []


def test_user_without_plan_cannot_train(raw: GymDatabase) -> None:
    with _error("no_plan"):
        raw.start_session(USER, None, START, START.date())
    assert raw.summary(USER, START.date())["total"] == 0


# --------------------------------------------------------------- separation


@pytest.fixture
def two(raw: GymDatabase) -> tuple[UserDB, UserDB, dict]:
    """User A with a finished and a running session, user B with an own plan."""
    raw.create_user_plan(USER, START, template=True)
    raw.create_user_plan(OTHER, START, name="B", freq_min=1, freq_max=1)
    a, b = UserDB(raw, USER), UserDB(raw, OTHER)
    b.add_exercise(None, WEIGHT)
    press = a.get_plans()[0]["exercises"][0]
    done = a.start_session(None, START, START.date())
    a.save_entry(done, press["id"], START, sets=3, reps=14, weight=65, rpe=7)
    a.finish_session(done, START + timedelta(minutes=30))
    running = a.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    entry_id = a.session_detail(done)["items"][0]["entry"]["id"]
    return a, b, {"press": press["id"], "done": done, "running": running, "entry": entry_id, "plan": press["plan_id"]}


def test_other_user_sees_nothing(two) -> None:
    a, b, ids = two
    assert [p["name"] for p in b.get_plans()] == ["B"]
    assert [e["name"] for e in b.get_plans()[0]["exercises"]] == ["Wadenheben"]
    assert b.list_sessions() == []
    assert b.running_session_id() is None
    assert b.summary(date(2026, 11, 4))["total"] == 0
    assert b.overview_stats(None, date(2026, 11, 4))["totals"]["count"] == 0
    assert b.export_rows() == []
    assert "Beinpresse" not in b.export_csv()
    assert len(a.export_rows()) == 3
    for read in (
        lambda: b.session_detail(ids["done"]),
        lambda: b.session_detail(ids["running"]),
        lambda: b.exercise_stats(ids["press"], None),
    ):
        with _error("not_found"):
            read()


def test_other_user_cannot_change_anything(two) -> None:
    a, b, ids = two
    now = START + timedelta(days=2)
    changes = (
        lambda: b.save_entry(ids["running"], ids["press"], now, sets=3, reps=10, weight=1, rpe=5),
        lambda: b.delete_entry(ids["entry"]),
        lambda: b.delete_session(ids["done"]),
        lambda: b.set_warmup_done(ids["running"], True),
        lambda: b.update_session(ids["done"], "fremd"),
        lambda: b.finish_session(ids["running"], now),
        lambda: b.cancel_session(ids["running"], now),
        lambda: b.add_session_exercise(ids["running"], WEIGHT),
        lambda: b.update_exercise(ids["press"], {**WEIGHT, "name": "Fremd"}),
        lambda: b.move_exercise(ids["press"], 1),
        lambda: b.archive_exercise(ids["press"]),
        lambda: b.restore_exercise(ids["press"]),
        lambda: b.add_exercise(ids["plan"], {**WEIGHT, "name": "Fremd"}),
        lambda: b.move_group(ids["plan"], "Arme", -1),
        lambda: b.start_session(ids["plan"], now, now.date()),
    )
    for change in changes:
        with _error("not_found"):
            change()
    # A's data is untouched.
    detail = a.session_detail(ids["done"])
    assert (detail["note"], detail["items"][0]["entry"]["weight"]) == ("", 65)
    assert a.running_session_id() == ids["running"]
    assert len(a.get_plans()[0]["exercises"]) == 10
    # B's own exercise cannot be used in A's session either.
    with _error("not_found"):
        a.save_entry(ids["running"], b.get_plans()[0]["exercises"][0]["id"], now, sets=3, reps=10, weight=40, rpe=6)


def test_running_session_and_plan_lock_are_per_user(two) -> None:
    a, b, ids = two
    # A trains right now: A's plan is locked, B's is not.
    with _error("session_running"):
        a.add_exercise(None, WEIGHT)
    b.add_exercise(None, {**WEIGHT, "name": "Klimmzüge", "kind": "reps"})
    # B starts an own session instead of getting A's running one.
    session_b = b.start_session(None, START + timedelta(days=2), date(2026, 11, 4))
    assert session_b != ids["running"]
    assert a.running_session_id() == ids["running"]
    assert b.running_session_id() == session_b
    calf = b.get_plans()[0]["exercises"][0]
    b.save_entry(session_b, calf["id"], START, sets=3, reps=12, weight=40, rpe=7)
    b.finish_session(session_b, START + timedelta(days=2, minutes=20))
    assert b.summary(date(2026, 11, 4))["total"] == 1
    assert a.summary(date(2026, 11, 4))["total"] == 1
    assert a.running_session_id() == ids["running"]


def test_same_exercise_name_for_different_users(two) -> None:
    _a, b, _ids = two
    b.add_exercise(None, {**WEIGHT, "name": "Beinpresse"})
    assert "Beinpresse" in [e["name"] for e in b.get_plans()[0]["exercises"]]
