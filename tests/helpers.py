"""Helpers shared by the tests."""

from __future__ import annotations

from functools import partial
from typing import Any

from custom_components.gym_tracker.db import GymDatabase

USER = "user-a"
OTHER = "user-b"

# Methods of GymDatabase that are not bound to a user.
GLOBAL_METHODS = {"close", "unassigned", "assign_unassigned", "plan_users", "create_plan"}


class UserDB:
    """View of the database for one user: passes the user id to every user-scoped method.

    Keeps the tests short (``db.start_session(None, ...)`` instead of
    ``db.start_session(USER, None, ...)``).
    """

    def __init__(self, database: GymDatabase, user_id: str = USER) -> None:
        """Bind a user."""
        self.raw = database
        self.user_id = user_id

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.raw, name)
        if name.startswith("_") or name in GLOBAL_METHODS or not callable(attr):
            return attr
        return partial(attr, self.user_id)


TABLES = ("plans", "exercises", "sessions", "entries", "entry_sets", "session_extras")


def make_v021_database(path) -> dict[str, int]:
    """Create a database as version 0.2.1 left it (schema 2), with realistic data.

    Contains the default plan, an archived spontaneous exercise, completed sessions with
    skipped exercises and a cancelled session.
    """
    import sqlite3

    from custom_components.gym_tracker.db import MIGRATIONS
    from custom_components.gym_tracker.seed import DEFAULT_PLAN

    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[0])
    conn.executescript(MIGRATIONS[1])
    conn.execute(
        "INSERT INTO plans (id, name, freq_min, freq_max, warmup, created_at)"
        " VALUES (1, 'Ganzkörper-Training', 2, 3, 'Ausfallschritte + Rotation', '2026-09-01T10:00:00+00:00')"
    )
    exercises = [*DEFAULT_PLAN["exercises"], {**DEFAULT_PLAN["exercises"][2], "name": "Klimmzüge", "archived": 1}]
    for number, ex in enumerate(exercises, start=1):
        conn.execute(
            "INSERT INTO exercises (id, plan_id, position, name, muscle_group, target_sets, reps_min, reps_max,"
            " duration_s, weighted, start_weight, setting, hint, archived)"
            " VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                number, number, ex["name"], ex["muscle_group"], ex["target_sets"], ex["reps_min"], ex["reps_max"],
                ex["duration_s"], int(ex["weighted"]), ex["start_weight"], ex["setting"], ex["hint"],
                ex.get("archived", 0),
            ),
        )  # fmt: skip
    entry_id = 0
    for number in range(1, 9):
        day = f"2026-10-{number * 3:02d}"
        status = "cancelled" if number == 4 else "completed"
        conn.execute(
            "INSERT INTO sessions (id, uuid, plan_id, started_at, ended_at, local_date, status, warmup_done)"
            " VALUES (?, ?, 1, ?, ?, ?, ?, 1)",
            (number, f"s{number}", f"{day}T17:00:00+00:00", f"{day}T18:00:00+00:00", day, status),
        )
        for ex_id, ex in enumerate(exercises[:10], start=1):
            entry_id += 1
            skipped = (number + ex_id) % 7 == 0
            conn.execute(
                "INSERT INTO entries (id, uuid, session_id, exercise_id, skipped, rpe, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, '', ?)",
                (
                    entry_id, f"e{entry_id}", number, ex_id, int(skipped), None if skipped else 7,
                    f"{day}T17:{ex_id:02d}:00+00:00",
                ),
            )
            if skipped:
                continue
            for set_no in range(1, ex["target_sets"] + 1):
                conn.execute(
                    "INSERT INTO entry_sets (entry_id, set_no, reps, seconds, weight) VALUES (?, ?, ?, ?, ?)",
                    (
                        entry_id, set_no, None if ex["duration_s"] else ex["reps_max"], ex["duration_s"],
                        ex["start_weight"] + number if ex["weighted"] else None,
                    ),
                )  # fmt: skip
    entry_id += 1
    conn.execute(
        "INSERT INTO entries (id, uuid, session_id, exercise_id, skipped, rpe, created_at)"
        " VALUES (?, 'extra', 2, 11, 0, 8, '2026-10-06T17:30:00+00:00')",
        (entry_id,),
    )
    conn.execute("INSERT INTO entry_sets (entry_id, set_no, reps) VALUES (?, 1, 6), (?, 2, 6)", (entry_id, entry_id))
    conn.execute("INSERT INTO session_extras (session_id, exercise_id, created) VALUES (2, 11, 1)")
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()
    return table_counts(path)


def table_counts(path) -> dict[str, int]:
    """Number of rows per table."""
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
    finally:
        conn.close()
