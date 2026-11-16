"""SQLite storage for Gym Tracker.

Synchronous on purpose and free of Home Assistant imports: the integration
calls it through the executor, the tests call it directly.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path
import sqlite3
import threading
from typing import Any
from uuid import uuid4

from . import logic
from .seed import DEFAULT_PLAN

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"

# One script per schema version; PRAGMA user_version stores how many ran.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE plans (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        freq_min INTEGER NOT NULL DEFAULT 1,
        freq_max INTEGER NOT NULL DEFAULT 1,
        warmup TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE exercises (
        id INTEGER PRIMARY KEY,
        plan_id INTEGER NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        name TEXT NOT NULL,
        muscle_group TEXT NOT NULL DEFAULT '',
        target_sets INTEGER NOT NULL,
        reps_min INTEGER,
        reps_max INTEGER,
        duration_s INTEGER,
        weighted INTEGER NOT NULL DEFAULT 0,
        start_weight REAL,
        setting TEXT NOT NULL DEFAULT '',
        hint TEXT NOT NULL DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE sessions (
        id INTEGER PRIMARY KEY,
        uuid TEXT NOT NULL UNIQUE,
        plan_id INTEGER NOT NULL REFERENCES plans(id),
        user_id TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT,
        local_date TEXT NOT NULL,
        status TEXT NOT NULL,
        warmup_done INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_sessions_date ON sessions(local_date);
    CREATE TABLE entries (
        id INTEGER PRIMARY KEY,
        uuid TEXT NOT NULL UNIQUE,
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id),
        skipped INTEGER NOT NULL DEFAULT 0,
        rpe INTEGER,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE (session_id, exercise_id)
    );
    CREATE INDEX idx_entries_exercise ON entries(exercise_id);
    CREATE TABLE entry_sets (
        entry_id INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
        set_no INTEGER NOT NULL,
        reps INTEGER,
        seconds INTEGER,
        weight REAL,
        PRIMARY KEY (entry_id, set_no)
    );
    """,
    # 2: exercises that were added spontaneously to a single session
    """
    CREATE TABLE session_extras (
        session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        created INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (session_id, exercise_id)
    );
    """,
)

KIND_WEIGHT = "weight"
KIND_REPS = "reps"
KIND_TIME = "time"
KINDS = (KIND_WEIGHT, KIND_REPS, KIND_TIME)


POINT_KEYS = (
    "session_id", "date", "rpe", "set_count", "reps", "seconds", "avg_reps",
    "max_reps", "max_seconds", "weight", "volume",
)  # fmt: skip


class GymError(Exception):
    """Validation error with a machine readable code."""

    def __init__(self, code: str, message: str) -> None:
        """Initialize the error."""
        super().__init__(message)
        self.code = code


def exercise_kind(exercise: dict[str, Any]) -> str:
    """Kind of an exercise: reps with weight, reps without weight, or time per set."""
    if exercise["duration_s"] is not None:
        return KIND_TIME
    return KIND_WEIGHT if exercise["weighted"] else KIND_REPS


def _int_between(value: Any, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def normalize_exercise(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the form data of an exercise and turn it into column values."""
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise GymError("name_required", "The exercise needs a name")
    kind = data.get("kind")
    if len(name) > 100 or kind not in KINDS:
        raise GymError("invalid_values", "Invalid name or kind")
    if not _int_between(data.get("target_sets"), 1, 20):
        raise GymError("invalid_values", "Sets must be between 1 and 20")
    columns: dict[str, Any] = {
        "name": name,
        "muscle_group": " ".join(str(data.get("muscle_group") or "").split())[:60],
        "setting": str(data.get("setting") or "").strip()[:300],
        "hint": str(data.get("hint") or "").strip()[:1000],
        "target_sets": data["target_sets"],
        "reps_min": None,
        "reps_max": None,
        "duration_s": None,
        "weighted": 0,
        "start_weight": None,
    }
    if kind == KIND_TIME:
        if not _int_between(data.get("duration_s"), 1, 3600):
            raise GymError("invalid_values", "Duration must be greater than 0")
        columns["duration_s"] = data["duration_s"]
        return columns
    low, high = data.get("reps_min"), data.get("reps_max")
    if not _int_between(low, 1, 999) or not _int_between(high, 1, 999) or low > high:
        raise GymError("invalid_values", "Repetitions must be greater than 0 and min <= max")
    columns["reps_min"], columns["reps_max"] = low, high
    if kind == KIND_WEIGHT:
        weight = data.get("start_weight")
        if not isinstance(weight, int | float) or isinstance(weight, bool) or not 0 < weight <= 1000:
            raise GymError("invalid_values", "Start weight must be greater than 0")
        columns["weighted"] = 1
        columns["start_weight"] = float(weight)
    return columns


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _locked[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with args[0]._lock:  # type: ignore[attr-defined]
            return func(*args, **kwargs)

    return wrapper


class GymDatabase:
    """All reads and writes of the training data."""

    def __init__(self, path: str | Path) -> None:
        """Open (and create or migrate) the database."""
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS, start=1):
            if number > version:
                self._conn.executescript(f"BEGIN;{script}PRAGMA user_version = {number};COMMIT;")

    @_locked
    def close(self) -> None:
        """Close the connection."""
        self._conn.close()

    def _all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ plans

    @_locked
    def seed_default_plan(self, now: datetime) -> bool:
        """Create the default plan if there is no plan yet."""
        if self._one("SELECT id FROM plans LIMIT 1"):
            return False
        self.create_plan(DEFAULT_PLAN, now)
        return True

    @_locked
    def create_plan(self, plan: dict[str, Any], now: datetime) -> int:
        """Create a plan with its exercises."""
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO plans (name, freq_min, freq_max, warmup, created_at) VALUES (?, ?, ?, ?, ?)",
                (plan["name"], plan["freq_min"], plan["freq_max"], plan.get("warmup", ""), _iso(now)),
            )
            plan_id = cur.lastrowid
            for position, ex in enumerate(plan.get("exercises", []), start=1):
                self._conn.execute(
                    "INSERT INTO exercises (plan_id, position, name, muscle_group, target_sets, reps_min,"
                    " reps_max, duration_s, weighted, start_weight, setting, hint)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan_id,
                        position,
                        ex["name"],
                        ex.get("muscle_group", ""),
                        ex["target_sets"],
                        ex.get("reps_min"),
                        ex.get("reps_max"),
                        ex.get("duration_s"),
                        int(bool(ex.get("weighted"))),
                        ex.get("start_weight"),
                        ex.get("setting", ""),
                        ex.get("hint", ""),
                    ),
                )
        assert plan_id is not None
        return plan_id

    def _exercise(self, row: dict[str, Any]) -> dict[str, Any]:
        row["weighted"] = bool(row["weighted"])
        row["archived"] = bool(row["archived"])
        row["kind"] = exercise_kind(row)
        return row

    def _plan_exercise(self, row: dict[str, Any]) -> dict[str, Any]:
        """Exercise for the plan editor: with the number of entries and the weight recorded last."""
        count = self._one("SELECT COUNT(*) AS n FROM entries WHERE exercise_id = ? AND skipped = 0", (row["id"],))
        last = self._one(
            "SELECT s.local_date AS date, (SELECT MAX(t.weight) FROM entry_sets t WHERE t.entry_id = e.id) AS weight"
            " FROM entries e JOIN sessions s ON s.id = e.session_id WHERE e.exercise_id = ? AND e.skipped = 0"
            " ORDER BY s.started_at DESC, s.id DESC LIMIT 1",
            (row["id"],),
        )
        assert count is not None
        row["entry_count"] = count["n"]
        row["last_weight"] = last["weight"] if last else None
        row["last_date"] = last["date"] if last else None
        return self._exercise(row)

    @_locked
    def get_plans(self) -> list[dict[str, Any]]:
        """Return all plans with their active exercises (in training order) and the archived ones."""
        plans = self._all("SELECT * FROM plans ORDER BY id")
        for plan in plans:
            plan["active"] = bool(plan["active"])
            rows = self._all("SELECT * FROM exercises WHERE plan_id = ? ORDER BY position, id", (plan["id"],))
            exercises = [self._plan_exercise(row) for row in rows]
            plan["exercises"] = [e for e in exercises if not e["archived"]]
            plan["archived_exercises"] = sorted(
                (e for e in exercises if e["archived"]), key=lambda e: e["name"].casefold()
            )
        return plans

    # ------------------------------------------------------------ plan editing

    def _require_unlocked(self) -> None:
        if self.running_session_id() is not None:
            raise GymError("session_running", "The plan cannot be changed while a session is running")

    def _exercise_row(self, exercise_id: int) -> dict[str, Any]:
        row = self._one("SELECT * FROM exercises WHERE id = ?", (exercise_id,))
        if row is None:
            raise GymError("not_found", f"Exercise {exercise_id} not found")
        return row

    def _check_name(self, plan_id: int, name: str, exclude_id: int | None = None, active_only: bool = False) -> None:
        """A name may exist only once; an archived exercise of that name has to be restored instead."""
        key = name.casefold()
        for row in self._all("SELECT id, name, archived FROM exercises WHERE plan_id = ?", (plan_id,)):
            if row["id"] == exclude_id or row["name"].casefold() != key:
                continue
            if not row["archived"]:
                raise GymError("name_exists", f"An exercise named {name} already exists")
            if not active_only:
                raise GymError("name_archived", f"An archived exercise named {name} exists, restore it instead")

    def _grouped(self, plan_id: int) -> list[tuple[str, list[int]]]:
        """Active exercises as consecutive groups; exercises without group always come last."""
        groups: dict[str, list[int]] = {}
        for row in self._all(
            "SELECT id, muscle_group FROM exercises WHERE plan_id = ? AND archived = 0 ORDER BY position, id",
            (plan_id,),
        ):
            groups.setdefault(row["muscle_group"], []).append(row["id"])
        if "" in groups:
            groups[""] = groups.pop("")
        return list(groups.items())

    def _write_order(self, groups: list[tuple[str, list[int]]]) -> None:
        """Number the active exercises 1..n without gaps, keeping every group together."""
        position = 0
        for _group, ids in groups:
            for exercise_id in ids:
                position += 1
                self._conn.execute("UPDATE exercises SET position = ? WHERE id = ?", (position, exercise_id))

    def _end_position(self, plan_id: int) -> int:
        row = self._one("SELECT COALESCE(MAX(position), 0) + 1 AS pos FROM exercises WHERE plan_id = ?", (plan_id,))
        assert row is not None
        return row["pos"]

    def _insert_exercise(self, plan_id: int, columns: dict[str, Any], archived: bool) -> int:
        values = {**columns, "plan_id": plan_id, "position": self._end_position(plan_id), "archived": int(archived)}
        cur = self._conn.execute(
            f"INSERT INTO exercises ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def _restore(self, exercise_id: int) -> None:
        """Put an archived exercise back into the plan, at the end of its group."""
        exercise = self._exercise_row(exercise_id)
        if not exercise["archived"]:
            return
        self._check_name(exercise["plan_id"], exercise["name"], exclude_id=exercise_id, active_only=True)
        self._conn.execute(
            "UPDATE exercises SET archived = 0, position = ? WHERE id = ?",
            (self._end_position(exercise["plan_id"]), exercise_id),
        )
        self._write_order(self._grouped(exercise["plan_id"]))

    @_locked
    def add_exercise(self, plan_id: int | None, data: dict[str, Any]) -> int:
        """Add an exercise at the end of its group (without group: at the end of the plan)."""
        self._require_unlocked()
        if plan_id is None:
            plan_id = self.default_plan_id()
        columns = normalize_exercise(data)
        self._check_name(plan_id, columns["name"])
        with self._conn:
            exercise_id = self._insert_exercise(plan_id, columns, archived=False)
            self._write_order(self._grouped(plan_id))
        return exercise_id

    @_locked
    def update_exercise(self, exercise_id: int, data: dict[str, Any]) -> None:
        """Change an exercise. Past entries stay untouched; the kind is fixed once entries exist."""
        self._require_unlocked()
        exercise = self._exercise_row(exercise_id)
        if exercise["archived"]:
            raise GymError("archived", "Restore the exercise before changing it")
        columns = normalize_exercise(data)
        self._check_name(exercise["plan_id"], columns["name"], exclude_id=exercise_id)
        if exercise_kind(columns) != exercise_kind(exercise) and self._one(
            "SELECT id FROM entries WHERE exercise_id = ? AND skipped = 0 LIMIT 1", (exercise_id,)
        ):
            raise GymError("kind_locked", "The kind cannot be changed once entries exist")
        if columns["muscle_group"] != exercise["muscle_group"]:
            columns["position"] = self._end_position(exercise["plan_id"])
        with self._conn:
            self._conn.execute(
                f"UPDATE exercises SET {', '.join(f'{key} = ?' for key in columns)} WHERE id = ?",
                (*columns.values(), exercise_id),
            )
            self._write_order(self._grouped(exercise["plan_id"]))

    @_locked
    def move_exercise(self, exercise_id: int, direction: int) -> None:
        """Move an exercise one step up (-1) or down (+1) within its group."""
        self._require_unlocked()
        exercise = self._exercise_row(exercise_id)
        groups = self._grouped(exercise["plan_id"])
        ids = next((ids for _group, ids in groups if exercise_id in ids), None)
        if ids is None:
            raise GymError("archived", "Archived exercises cannot be moved")
        index = ids.index(exercise_id)
        target = index + direction
        if not 0 <= target < len(ids):
            raise GymError("edge", "The exercise is already at the edge of its group")
        ids[index], ids[target] = ids[target], ids[index]
        with self._conn:
            self._write_order(groups)

    @_locked
    def move_group(self, plan_id: int | None, group: str, direction: int) -> None:
        """Move a whole group one step up (-1) or down (+1)."""
        self._require_unlocked()
        if plan_id is None:
            plan_id = self.default_plan_id()
        groups = self._grouped(plan_id)
        names = [name for name, _ids in groups]
        if group not in names:
            raise GymError("not_found", f"Group {group} not found")
        index = names.index(group)
        target = index + direction
        if not group or not 0 <= target < len(groups) or not names[target]:
            raise GymError("edge", "The group cannot be moved further")
        groups[index], groups[target] = groups[target], groups[index]
        with self._conn:
            self._write_order(groups)

    @_locked
    def archive_exercise(self, exercise_id: int) -> None:
        """Take an exercise out of the plan. Its entries stay in history and statistics."""
        self._require_unlocked()
        exercise = self._exercise_row(exercise_id)
        with self._conn:
            self._conn.execute("UPDATE exercises SET archived = 1 WHERE id = ?", (exercise_id,))
            self._write_order(self._grouped(exercise["plan_id"]))

    @_locked
    def restore_exercise(self, exercise_id: int) -> None:
        """Bring an archived exercise back, with its values and its history."""
        self._require_unlocked()
        with self._conn:
            self._restore(exercise_id)

    @_locked
    def add_session_exercise(
        self, session_id: int, data: dict[str, Any] | None = None, exercise_id: int | None = None
    ) -> int:
        """Add a spontaneous exercise to the running session.

        It stays outside the plan (archived) until the session is finished and
        the user decides to adopt it. An archived exercise can be reused.
        """
        session = self._session_row(session_id)
        if session["status"] != STATUS_RUNNING:
            raise GymError("not_running", "Session is not running")
        with self._conn:
            if exercise_id is not None:
                exercise = self._exercise_row(exercise_id)
                if exercise["plan_id"] != session["plan_id"] or not exercise["archived"]:
                    raise GymError("name_exists", "The exercise is already part of the plan")
                created = 0
            else:
                columns = normalize_exercise(data or {})
                self._check_name(session["plan_id"], columns["name"])
                exercise_id = self._insert_exercise(session["plan_id"], columns, archived=True)
                created = 1
            self._conn.execute(
                "INSERT OR IGNORE INTO session_extras (session_id, exercise_id, created) VALUES (?, ?, ?)",
                (session_id, exercise_id, created),
            )
        return exercise_id

    def _drop_unused_extras(self, session_id: int) -> list[int]:
        """Ids of exercises created for this session only, to be removed with it if never recorded."""
        rows = self._all("SELECT exercise_id FROM session_extras WHERE session_id = ? AND created = 1", (session_id,))
        return [row["exercise_id"] for row in rows]

    @_locked
    def default_plan_id(self) -> int:
        """Plan used when the panel starts a session without choosing one."""
        row = self._one("SELECT id FROM plans WHERE active = 1 ORDER BY id LIMIT 1")
        if row is None:
            raise GymError("no_plan", "No training plan available")
        return row["id"]

    # --------------------------------------------------------------- sessions

    def _session_row(self, session_id: int) -> dict[str, Any]:
        row = self._one(
            "SELECT s.*, p.name AS plan_name, p.warmup AS warmup FROM sessions s"
            " JOIN plans p ON p.id = s.plan_id WHERE s.id = ?",
            (session_id,),
        )
        if row is None:
            raise GymError("not_found", f"Session {session_id} not found")
        row["warmup_done"] = bool(row["warmup_done"])
        return row

    @_locked
    def running_session_id(self) -> int | None:
        """Id of the session in progress, if any."""
        row = self._one("SELECT id FROM sessions WHERE status = ? ORDER BY id DESC LIMIT 1", (STATUS_RUNNING,))
        return row["id"] if row else None

    @_locked
    def start_session(self, plan_id: int | None, now: datetime, local_date: date) -> int:
        """Start a session; an already running one is returned instead."""
        running = self.running_session_id()
        if running is not None:
            return running
        if plan_id is None:
            plan_id = self.default_plan_id()
        elif not self._one("SELECT id FROM plans WHERE id = ?", (plan_id,)):
            raise GymError("not_found", f"Plan {plan_id} not found")
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO sessions (uuid, plan_id, started_at, local_date, status) VALUES (?, ?, ?, ?, ?)",
                (uuid4().hex, plan_id, _iso(now), local_date.isoformat(), STATUS_RUNNING),
            )
        assert cur.lastrowid is not None
        return cur.lastrowid

    @_locked
    def set_warmup_done(self, session_id: int, done: bool) -> None:
        """Tick off the warm-up."""
        self._session_row(session_id)
        with self._conn:
            self._conn.execute("UPDATE sessions SET warmup_done = ? WHERE id = ?", (int(done), session_id))

    @_locked
    def update_session(self, session_id: int, note: str) -> None:
        """Change the note of a session."""
        self._session_row(session_id)
        with self._conn:
            self._conn.execute("UPDATE sessions SET note = ? WHERE id = ?", (note, session_id))

    @_locked
    def finish_session(self, session_id: int, now: datetime, adopt: tuple[int, ...] | list[int] = ()) -> None:
        """Mark a running session as completed.

        Spontaneous exercises listed in ``adopt`` become regular plan exercises,
        the others stay archived (their entries are kept).
        """
        session = self._session_row(session_id)
        if session["status"] != STATUS_RUNNING:
            raise GymError("not_running", "Session is not running")
        last = self._one(
            "SELECT MAX(created_at) AS last FROM entries WHERE session_id = ? AND skipped = 0", (session_id,)
        )
        if last is None or last["last"] is None:
            raise GymError("empty_session", "No exercise recorded yet")
        end = logic.session_end(now, datetime.fromisoformat(last["last"]))
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?", (STATUS_COMPLETED, _iso(end), session_id)
            )
            extras = self._all("SELECT exercise_id FROM session_extras WHERE session_id = ?", (session_id,))
            for row in extras:
                if row["exercise_id"] in adopt:
                    self._restore(row["exercise_id"])

    @_locked
    def cancel_session(self, session_id: int, now: datetime) -> bool:
        """Cancel a running session. Without any entry it is removed entirely.

        Returns True if the session still exists afterwards.
        """
        session = self._session_row(session_id)
        if session["status"] != STATUS_RUNNING:
            raise GymError("not_running", "Session is not running")
        last = self._one(
            "SELECT MAX(created_at) AS last, COUNT(*) AS n FROM entries WHERE session_id = ?", (session_id,)
        )
        assert last is not None
        if not last["n"]:
            self.delete_session(session_id)
            return False
        end = logic.session_end(now, datetime.fromisoformat(last["last"]))
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?", (STATUS_CANCELLED, _iso(end), session_id)
            )
        return True

    @_locked
    def delete_session(self, session_id: int) -> None:
        """Delete a session including its entries."""
        self._session_row(session_id)
        created = self._drop_unused_extras(session_id)
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            for exercise_id in created:
                self._conn.execute(
                    "DELETE FROM exercises WHERE id = ? AND archived = 1"
                    " AND NOT EXISTS (SELECT 1 FROM entries WHERE exercise_id = ?)",
                    (exercise_id, exercise_id),
                )

    # ---------------------------------------------------------------- entries

    def _entry(self, row: dict[str, Any]) -> dict[str, Any]:
        sets = self._all(
            "SELECT set_no, reps, seconds, weight FROM entry_sets WHERE entry_id = ? ORDER BY set_no", (row["id"],)
        )
        row["skipped"] = bool(row["skipped"])
        row["sets"] = sets
        row.update(logic.summarize_sets(sets))
        return row

    def _previous_entry(self, exercise_id: int, session: dict[str, Any]) -> dict[str, Any] | None:
        """Last recorded (not skipped) entry of an exercise before the given session."""
        row = self._one(
            "SELECT e.*, s.local_date AS date FROM entries e JOIN sessions s ON s.id = e.session_id"
            " WHERE e.exercise_id = ? AND e.skipped = 0"
            " AND (s.started_at < ? OR (s.started_at = ? AND s.id < ?))"
            " ORDER BY s.started_at DESC, s.id DESC LIMIT 1",
            (exercise_id, session["started_at"], session["started_at"], session["id"]),
        )
        return self._entry(row) if row else None

    @_locked
    def save_entry(
        self,
        session_id: int,
        exercise_id: int,
        now: datetime,
        *,
        skipped: bool = False,
        sets: int | None = None,
        reps: int | None = None,
        seconds: int | None = None,
        weight: float | None = None,
        rpe: int | None = None,
        note: str = "",
        entry_uuid: str | None = None,
    ) -> int:
        """Create or replace the entry of an exercise within a session.

        Saving is idempotent per (session, exercise), so a retried request
        never creates a duplicate.
        """
        session = self._session_row(session_id)
        exercise = self._one("SELECT * FROM exercises WHERE id = ?", (exercise_id,))
        if exercise is None or exercise["plan_id"] != session["plan_id"]:
            raise GymError("not_found", f"Exercise {exercise_id} not found in this plan")
        timed = exercise["duration_s"] is not None
        rows: list[tuple[int, int | None, int | None, float | None]] = []
        if skipped:
            rpe = None
        else:
            if sets is None or not 1 <= sets <= 20:
                raise GymError("invalid", "Number of sets must be between 1 and 20")
            if rpe is not None and not 1 <= rpe <= 10:
                raise GymError("invalid", "RPE must be between 1 and 10")
            amount = seconds if timed else reps
            if amount is None or not 0 <= amount <= 3600:
                raise GymError("invalid", "Seconds are required" if timed else "Repetitions are required")
            if exercise["weighted"]:
                if weight is None or not 0 <= weight <= 1000:
                    raise GymError("invalid", "Weight is required")
            else:
                weight = None
            rows = [
                (number, None if timed else amount, amount if timed else None, weight)
                for number in range(1, sets + 1)
            ]
        with self._conn:
            existing = self._one(
                "SELECT id FROM entries WHERE session_id = ? AND exercise_id = ?", (session_id, exercise_id)
            )
            if existing:
                entry_id = existing["id"]
                self._conn.execute(
                    "UPDATE entries SET skipped = ?, rpe = ?, note = ? WHERE id = ?",
                    (int(skipped), rpe, note, entry_id),
                )
                self._conn.execute("DELETE FROM entry_sets WHERE entry_id = ?", (entry_id,))
            else:
                cur = self._conn.execute(
                    "INSERT INTO entries (uuid, session_id, exercise_id, skipped, rpe, note, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entry_uuid or uuid4().hex, session_id, exercise_id, int(skipped), rpe, note, _iso(now)),
                )
                entry_id = cur.lastrowid
            self._conn.executemany(
                "INSERT INTO entry_sets (entry_id, set_no, reps, seconds, weight) VALUES (?, ?, ?, ?, ?)",
                [(entry_id, *row) for row in rows],
            )
        assert entry_id is not None
        return entry_id

    @_locked
    def delete_entry(self, entry_id: int) -> int:
        """Delete an entry and return the id of its session."""
        row = self._one("SELECT session_id FROM entries WHERE id = ?", (entry_id,))
        if row is None:
            raise GymError("not_found", f"Entry {entry_id} not found")
        with self._conn:
            self._conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))
        return row["session_id"]

    # ---------------------------------------------------------------- queries

    @_locked
    def session_detail(self, session_id: int) -> dict[str, Any]:
        """A session with one item per exercise: plan data, defaults, previous and current entry."""
        session = self._session_row(session_id)
        extras = [
            row["exercise_id"]
            for row in self._all(
                "SELECT exercise_id FROM session_extras WHERE session_id = ? ORDER BY rowid", (session_id,)
            )
        ]
        exercises = self._all(
            "SELECT * FROM exercises WHERE plan_id = ? AND (archived = 0 OR id IN"
            " (SELECT exercise_id FROM entries WHERE session_id = ?) OR id IN"
            " (SELECT exercise_id FROM session_extras WHERE session_id = ?)) ORDER BY archived, position, id",
            (session["plan_id"], session_id, session_id),
        )
        # Spontaneous exercises always come after the regular ones, in the order they were added.
        exercises.sort(key=lambda row: extras.index(row["id"]) + 1 if row["id"] in extras else 0)
        entries = {
            row["exercise_id"]: self._entry(row)
            for row in self._all("SELECT * FROM entries WHERE session_id = ?", (session_id,))
        }
        items = []
        changes = []
        for exercise in map(self._exercise, exercises):
            previous = self._previous_entry(exercise["id"], session)
            entry = entries.get(exercise["id"])
            change = logic.weight_change(exercise, previous, entry)
            if change:
                changes.append({"exercise_id": exercise["id"], "name": exercise["name"], **change})
            items.append(
                {
                    "exercise": exercise,
                    "adhoc": exercise["id"] in extras,
                    "defaults": logic.next_defaults(exercise, previous),
                    "previous": previous,
                    "entry": entry,
                    "weight_change": change,
                }
            )
        done = [e for e in entries.values() if not e["skipped"]]
        rpes = [e["rpe"] for e in done if e["rpe"] is not None]
        duration = None
        if session["ended_at"]:
            delta = datetime.fromisoformat(session["ended_at"]) - datetime.fromisoformat(session["started_at"])
            duration = int(delta.total_seconds())
        session["items"] = items
        session["summary"] = {
            "duration_s": duration,
            "volume": round(sum(e["volume"] for e in done), 2),
            "done": len(done),
            "skipped": len(entries) - len(done),
            "total": len(items),
            "avg_rpe": round(sum(rpes) / len(rpes), 2) if rpes else None,
            "weight_changes": changes,
        }
        return session

    @_locked
    def list_sessions(
        self,
        *,
        since: date | None = None,
        statuses: tuple[str, ...] = (STATUS_COMPLETED, STATUS_CANCELLED),
        limit: int = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Sessions with their key figures, newest first."""
        marks = ",".join("?" * len(statuses))
        rows = self._all(
            "SELECT s.id, s.local_date AS date, s.started_at, s.ended_at, s.status, s.note, p.name AS plan_name,"
            " (SELECT COUNT(*) FROM entries e WHERE e.session_id = s.id AND e.skipped = 0) AS done,"
            " (SELECT COUNT(*) FROM entries e WHERE e.session_id = s.id AND e.skipped = 1) AS skipped,"
            " (SELECT AVG(e.rpe) FROM entries e WHERE e.session_id = s.id AND e.skipped = 0) AS avg_rpe,"
            " (SELECT COALESCE(SUM(t.reps * t.weight), 0) FROM entry_sets t JOIN entries e ON e.id = t.entry_id"
            "   WHERE e.session_id = s.id AND e.skipped = 0) AS volume"
            " FROM sessions s JOIN plans p ON p.id = s.plan_id"
            f" WHERE s.status IN ({marks}) AND s.local_date >= ?"
            " ORDER BY s.started_at DESC, s.id DESC LIMIT ? OFFSET ?",
            (*statuses, since.isoformat() if since else "", limit, offset),
        )
        for row in rows:
            row["volume"] = round(row["volume"], 2)
            row["avg_rpe"] = round(row["avg_rpe"], 2) if row["avg_rpe"] is not None else None
            row["duration_s"] = None
            if row["ended_at"]:
                delta = datetime.fromisoformat(row["ended_at"]) - datetime.fromisoformat(row["started_at"])
                row["duration_s"] = int(delta.total_seconds())
        return rows

    @_locked
    def summary(self, today: date) -> dict[str, Any]:
        """Figures for the sensors and the start screen (completed sessions only)."""
        week = logic.week_start(today)
        month = logic.month_start(today)

        def count(start: date | None = None) -> int:
            row = self._one(
                "SELECT COUNT(*) AS n FROM sessions WHERE status = ? AND local_date >= ?",
                (STATUS_COMPLETED, start.isoformat() if start else ""),
            )
            assert row is not None
            return row["n"]

        last = self._one("SELECT MAX(local_date) AS last FROM sessions WHERE status = ?", (STATUS_COMPLETED,))
        plan = self._one("SELECT freq_min, freq_max FROM plans WHERE active = 1 ORDER BY id LIMIT 1")
        return {
            "last_date": last["last"] if last else None,
            "week": count(week),
            "month": count(month),
            "total": count(),
            "week_start": week.isoformat(),
            "freq_min": plan["freq_min"] if plan else None,
            "freq_max": plan["freq_max"] if plan else None,
        }

    @_locked
    def exercise_history(self, exercise_id: int) -> list[dict[str, Any]]:
        """One point per finished session in which the exercise was recorded, oldest first."""
        rows = self._all(
            "SELECT e.*, s.local_date AS date FROM entries e JOIN sessions s ON s.id = e.session_id"
            " WHERE e.exercise_id = ? AND e.skipped = 0 AND s.status != ?"
            " ORDER BY s.started_at, s.id",
            (exercise_id, STATUS_RUNNING),
        )
        points = []
        for row in rows:
            entry = self._entry(row)
            points.append({key: entry[key] for key in POINT_KEYS})
        return points

    @_locked
    def exercise_stats(self, exercise_id: int, since: date | None) -> dict[str, Any]:
        """History of an exercise within a range plus all-time records."""
        exercise = self._one("SELECT * FROM exercises WHERE id = ?", (exercise_id,))
        if exercise is None:
            raise GymError("not_found", f"Exercise {exercise_id} not found")
        history = self.exercise_history(exercise_id)
        records = logic.find_records(history)
        points = [p for p in history if since is None or p["date"] >= since.isoformat()]
        for point in points:
            point["records"] = [name for name, rec in records.items() if rec["session_id"] == point["session_id"]]
        return {"exercise": self._exercise(exercise), "points": points, "records": records}

    @_locked
    def overview_stats(self, since: date | None, today: date) -> dict[str, Any]:
        """Completed sessions of a range, grouped into weeks or months."""
        sessions = list(reversed(self.list_sessions(since=since, statuses=(STATUS_COMPLETED,))))
        start = since or (date.fromisoformat(sessions[0]["date"]) if sessions else today)
        granularity = logic.choose_granularity(start, today)
        rpes = [s["avg_rpe"] for s in sessions if s["avg_rpe"] is not None]
        durations = [s["duration_s"] for s in sessions if s["duration_s"] is not None]
        weeks = max(((today - start) + timedelta(days=1)).days / 7, 1)
        plan = self._one("SELECT freq_min, freq_max FROM plans WHERE active = 1 ORDER BY id LIMIT 1")
        return {
            "start": start.isoformat(),
            "end": today.isoformat(),
            "granularity": granularity,
            "sessions": sessions,
            "buckets": logic.bucketize(sessions, start, today, granularity),
            "totals": {
                "count": len(sessions),
                "volume": round(sum(s["volume"] for s in sessions), 2),
                "avg_rpe": round(sum(rpes) / len(rpes), 2) if rpes else None,
                "avg_duration_s": int(sum(durations) / len(durations)) if durations else None,
                "per_week": round(len(sessions) / weeks, 2),
            },
            "freq_min": plan["freq_min"] if plan else None,
            "freq_max": plan["freq_max"] if plan else None,
        }

    @_locked
    def export_rows(self) -> list[dict[str, Any]]:
        """All data, one row per set (skipped exercises: one row without set)."""
        return self._all(
            "SELECT s.local_date AS date, s.started_at, s.ended_at, s.status, p.name AS plan,"
            " x.name AS exercise, x.muscle_group, t.set_no, t.reps, t.seconds, t.weight, e.rpe, e.skipped, e.note,"
            " x.archived"
            " FROM entries e JOIN sessions s ON s.id = e.session_id JOIN plans p ON p.id = s.plan_id"
            " JOIN exercises x ON x.id = e.exercise_id LEFT JOIN entry_sets t ON t.entry_id = e.id"
            " ORDER BY s.started_at, s.id, x.position, t.set_no"
        )

    @_locked
    def export_csv(self) -> str:
        """All data as CSV text."""
        return logic.build_csv(self.export_rows())
