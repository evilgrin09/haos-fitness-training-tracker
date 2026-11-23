"""Pure training logic: weight defaults, volume, records, time buckets, CSV.

No Home Assistant imports, so everything here is testable on its own.
"""

from __future__ import annotations

from collections import Counter
import csv
from datetime import date, datetime, timedelta
import io
from typing import Any

CARDIO_PHASES = ("warmup", "cooldown")
CARDIO_PHASE_LABELS = {"warmup": "Aufwärmen", "cooldown": "Auslaufen"}
CARDIO_DEVICES = {"crosstrainer": "Crosstrainer", "bike": "Fahrrad", "treadmill": "Laufband"}
CARDIO_MIN_MINUTES = 1
CARDIO_MAX_MINUTES = 180

RANGE_DAYS: dict[str, int | None] = {"4w": 28, "3m": 91, "6m": 182, "1y": 365, "all": None}

# If a session is finished long after the last recorded exercise (forgot to
# tap "finish" in the gym), the last entry marks the end instead.
STALE_FINISH = timedelta(hours=2)


def find_legacy_owner(users: list[dict[str, Any]], name: str) -> str | None:
    """Id of the one user whose display name or user name equals ``name`` (case-insensitive).

    Returns None if nobody or more than one user matches: the data is then left unassigned
    instead of guessing.
    """
    key = name.casefold()
    matches = {
        user["id"]
        for user in users
        if (user.get("name") or "").casefold() == key
        or any(username.casefold() == key for username in user.get("usernames", []))
    }
    return matches.pop() if len(matches) == 1 else None


def range_start(key: str, today: date) -> date | None:
    """First day of the evaluation range, None for "all"."""
    days = RANGE_DAYS[key]
    return None if days is None else today - timedelta(days=days - 1)


def week_start(day: date) -> date:
    """Monday of the week of the given day."""
    return day - timedelta(days=day.weekday())


def month_start(day: date) -> date:
    """First day of the month of the given day."""
    return day.replace(day=1)


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


def summarize_sets(sets: list[dict[str, Any]]) -> dict[str, Any]:
    """Condense the sets of an entry into the values shown and charted."""
    reps = [s["reps"] for s in sets if s.get("reps") is not None]
    seconds = [s["seconds"] for s in sets if s.get("seconds") is not None]
    weights = [s["weight"] for s in sets if s.get("weight") is not None]
    volume = sum(s["reps"] * s["weight"] for s in sets if s.get("reps") is not None and s.get("weight") is not None)
    return {
        "min_reps": min(reps) if reps else None,
        "min_seconds": min(seconds) if seconds else None,
        "set_count": len(sets),
        # Typical value per set: the most frequent one, on a tie the first.
        "reps": Counter(reps).most_common(1)[0][0] if reps else None,
        "seconds": Counter(seconds).most_common(1)[0][0] if seconds else None,
        "avg_reps": round(sum(reps) / len(reps), 2) if reps else None,
        "max_reps": max(reps) if reps else None,
        "max_seconds": max(seconds) if seconds else None,
        "weight": max(weights) if weights else None,
        "volume": round(volume, 2),
    }


def next_defaults(exercise: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Values the input form is prefilled with.

    Sets come from the plan. Weight, repetitions and seconds are the values recorded
    last for this exercise; before the very first entry the plan applies (start weight,
    lower end of the range, target time).
    """
    weight = None
    if exercise["weighted"]:
        weight = exercise["start_weight"]
        if previous is not None and previous.get("weight") is not None:
            weight = previous["weight"]
    reps = exercise["reps_min"]
    seconds = exercise["duration_s"]
    if previous is not None:
        if reps is not None and previous.get("reps") is not None:
            reps = previous["reps"]
        if seconds is not None and previous.get("seconds") is not None:
            seconds = previous["seconds"]
    return {"sets": exercise["target_sets"], "reps": reps, "seconds": seconds, "weight": weight}


def weight_change(
    exercise: dict[str, Any], previous: dict[str, Any] | None, entry: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Difference between the recorded weight and the weight that applied before."""
    if entry is None or entry.get("skipped") or entry.get("weight") is None:
        return None
    before = next_defaults(exercise, previous)["weight"]
    if before is None or abs(entry["weight"] - before) < 1e-9:
        return None
    return {"from": before, "to": entry["weight"], "delta": round(entry["weight"] - before, 3)}


def session_end(now: datetime, last_entry_at: datetime | None) -> datetime:
    """End time of a session that is finished now."""
    if last_entry_at is not None and now - last_entry_at > STALE_FINISH:
        return last_entry_at
    return now


# ------------------------------------------------------------- progression
#
# Suggestions are friendly proposals for the next session of an exercise. They are based on
# completed sessions only (skipped exercises and cancelled sessions do not count).

PROGRESSION_MIN_SESSIONS = 2  # no suggestion at all with less data
WEIGHT_RPE_MAX = 8  # all sets at the top of the range and RPE <= 8: add weight
DOUBLE_STEP_RPE_MAX = 6  # twice in a row at the top with RPE <= 6: double step
WEIGHT_STEP_KG = 2.5
WEIGHT_STEP_LIGHT_KG = 1.0  # for weights below LIGHT_WEIGHT_BELOW_KG
LIGHT_WEIGHT_BELOW_KG = 10
REPS_RPE_MAX = 7  # below the top of the range and RPE <= 7: 1–2 reps more
TIME_RPE_MAX = 7  # target time reached and RPE <= 7: + TIME_STEP_S
TIME_STEP_S = 5
STALL_DAYS = 28  # stagnation: no progress within 4 weeks ...
STALL_SESSIONS = 6  # ... or within 6 sessions (whichever happens first)
STALL_PUSH_RPE_MAX = 7  # stagnation with average RPE <= 7: suggest to progress
STALL_RECOVERY_RPE_MIN = 9  # stagnation with average RPE >= 9: check form, sleep and recovery
SETS_FOR_EXTRA_SET = 2  # only exercises with this many target sets get "one set more"

# Suggestions that only show a hint; they can be dismissed but not "applied".
HINT_RULES = {"recovery", "bodyweight", "stall_push"}


def _beats(point: dict[str, Any], base: dict[str, Any], kind: str) -> bool:
    """Whether a session shows progress compared to another one."""
    if kind == "time":
        return (point.get("seconds") or 0) > (base.get("seconds") or 0)
    if kind == "weight":
        weight, base_weight = point.get("weight") or 0, base.get("weight") or 0
        return weight > base_weight or (weight == base_weight and (point.get("reps") or 0) > (base.get("reps") or 0))
    return (point.get("reps") or 0) > (base.get("reps") or 0)


def stagnation(kind: str, history: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """No progress within the last 4 weeks or within the last 6 sessions, whichever is reached first.

    ``history`` holds the completed sessions of an exercise, oldest first. A window counts if
    it has at least two sessions and no session in it beats the first one of the window. The
    4-week window needs data reaching back at least 4 weeks.
    """
    windows = []
    if len(history) >= STALL_SESSIONS:
        windows.append(history[-STALL_SESSIONS:])
    start = today - timedelta(days=STALL_DAYS)
    if history and date.fromisoformat(history[0]["date"]) <= start:
        windows.append([p for p in history if date.fromisoformat(p["date"]) >= start])
    for window in windows:
        if len(window) >= 2 and not any(_beats(p, window[0], kind) for p in window[1:]):
            rpes = [p["rpe"] for p in window if p.get("rpe") is not None]
            return {
                "sessions": len(window),
                "since": window[0]["date"],
                "avg_rpe": round(sum(rpes) / len(rpes), 1) if rpes else None,
            }
    return None


def _at_top(exercise: dict[str, Any], point: dict[str, Any]) -> bool:
    """All target sets done with at least the upper end of the repetition range."""
    return (
        point.get("set_count", 0) >= exercise["target_sets"]
        and point.get("min_reps") is not None
        and point["min_reps"] >= exercise["reps_max"]
    )


def weight_step(weight: float) -> float:
    """Smallest sensible weight step."""
    return WEIGHT_STEP_LIGHT_KG if weight < LIGHT_WEIGHT_BELOW_KG else WEIGHT_STEP_KG


def recommend(exercise: dict[str, Any], history: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    """Suggestion for the next session of an exercise, or None.

    Returns ``{"rule": ..., "data": {...}}``; at most one suggestion per exercise, the first
    matching rule wins: recovery hint, double weight step, weight step, more reps, more
    time, bodyweight hint, one set more, stagnation hint.
    """
    if len(history) < PROGRESSION_MIN_SESSIONS:
        return None
    kind = "time" if exercise["duration_s"] is not None else "weight" if exercise["weighted"] else "reps"
    last, before = history[-1], history[-2]
    rpe = last.get("rpe")
    stall = stagnation(kind, history, today)
    base = {"rpe": rpe, "reps": last.get("reps"), "seconds": last.get("seconds"), "weight": last.get("weight")}

    if stall and stall["avg_rpe"] is not None and stall["avg_rpe"] >= STALL_RECOVERY_RPE_MIN:
        return {"rule": "recovery", "data": {**base, **stall}}
    if rpe is not None:
        if kind == "weight" and last.get("weight") is not None and _at_top(exercise, last) and rpe <= WEIGHT_RPE_MAX:
            step = weight_step(last["weight"])
            double = (
                rpe <= DOUBLE_STEP_RPE_MAX
                and _at_top(exercise, before)
                and before.get("rpe") is not None
                and before["rpe"] <= DOUBLE_STEP_RPE_MAX
            )
            step = step * 2 if double else step
            return {
                "rule": "weight_double" if double else "weight",
                "data": {**base, "step": step, "suggested_weight": round(last["weight"] + step, 2),
                         "suggested_reps": exercise["reps_min"]},
            }  # fmt: skip
        if kind in ("weight", "reps") and (last.get("reps") or 0) < exercise["reps_max"] and rpe <= REPS_RPE_MAX:
            return {"rule": "reps", "data": {**base, "suggested_reps": min(last["reps"] + 1, exercise["reps_max"])}}
        reached = last.get("min_seconds") is not None and last["min_seconds"] >= exercise["duration_s"]
        if kind == "time" and reached and rpe <= TIME_RPE_MAX:
            return {"rule": "time", "data": {**base, "suggested_seconds": last["seconds"] + TIME_STEP_S}}
    if kind == "reps" and (last.get("reps") or 0) > exercise["reps_max"]:
        return {"rule": "bodyweight", "data": {**base, "reps_max": exercise["reps_max"]}}
    if stall and stall["avg_rpe"] is not None and stall["avg_rpe"] <= STALL_PUSH_RPE_MAX:
        if exercise["target_sets"] == SETS_FOR_EXTRA_SET:
            return {"rule": "sets", "data": {**base, **stall, "suggested_sets": exercise["target_sets"] + 1}}
        return {"rule": "stall_push", "data": {**base, **stall}}
    return None


def apply_recommendation(defaults: dict[str, Any], recommendation: dict[str, Any] | None) -> dict[str, Any]:
    """Prefill of the input form once a suggestion was accepted for the session."""
    if not recommendation or recommendation.get("decision") != "accepted":
        return defaults
    data = recommendation["data"]
    changed = dict(defaults)
    if recommendation["rule"] in ("weight", "weight_double"):
        # After a weight increase start again at the lower end of the range.
        changed["weight"] = data["suggested_weight"]
        changed["reps"] = data["suggested_reps"]
    elif recommendation["rule"] == "reps":
        changed["reps"] = data["suggested_reps"]
    elif recommendation["rule"] == "time":
        changed["seconds"] = data["suggested_seconds"]
    elif recommendation["rule"] == "sets":
        changed["sets"] = data["suggested_sets"]
    return changed


RECORD_KEYS = {"weight": "weight", "volume": "volume", "reps": "max_reps", "seconds": "max_seconds"}


def find_records(points: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Best values over a chronological list of exercise points (first occurrence wins)."""
    records: dict[str, dict[str, Any]] = {}
    for name, key in RECORD_KEYS.items():
        best = None
        for point in points:
            value = point.get(key)
            if value is None or value <= 0:
                continue
            if best is None or value > best[key]:
                best = point
        if best is not None:
            records[name] = {"value": best[key], "date": best["date"], "session_id": best["session_id"]}
    return records


def choose_granularity(start: date, end: date) -> str:
    """Weekly buckets for up to about half a year, monthly beyond that."""
    return "week" if (end - start).days <= 190 else "month"


def bucketize(sessions: list[dict[str, Any]], start: date, end: date, granularity: str) -> list[dict[str, Any]]:
    """Group sessions into consecutive weeks or months, including empty ones."""
    first = week_start(start) if granularity == "week" else month_start(start)
    buckets: list[dict[str, Any]] = []
    cursor = first
    while cursor <= end:
        nxt = cursor + timedelta(days=7) if granularity == "week" else _next_month(cursor)
        members = [s for s in sessions if cursor <= date.fromisoformat(s["date"]) < nxt]
        rpes = [s["avg_rpe"] for s in members if s.get("avg_rpe") is not None]
        buckets.append(
            {
                "start": cursor.isoformat(),
                "end": (nxt - timedelta(days=1)).isoformat(),
                "count": len(members),
                "volume": round(sum(s.get("volume") or 0 for s in members), 2),
                "avg_rpe": round(sum(rpes) / len(rpes), 2) if rpes else None,
            }
        )
        cursor = nxt
    return buckets


def cardio_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cardio minutes in total, per phase and per device."""
    return {
        "minutes": sum(row["minutes"] for row in rows),
        "by_phase": {phase: sum(r["minutes"] for r in rows if r["phase"] == phase) for phase in CARDIO_PHASES},
        "by_device": {device: sum(r["minutes"] for r in rows if r["device"] == device) for device in CARDIO_DEVICES},
    }


def cardio_stats(
    rows: list[dict[str, Any]], sessions: list[dict[str, Any]], start: date, end: date, granularity: str
) -> dict[str, Any]:
    """Cardio of a range: totals, share of the training time, weekly/monthly buckets and minutes per session.

    ``rows`` are the cardio entries (with ``date``), ``sessions`` the completed sessions of the range.
    Cardio is kept apart from volume, intensity and records on purpose.
    """
    totals = cardio_totals(rows)
    duration = sum(s["duration_s"] or 0 for s in sessions)
    first = week_start(start) if granularity == "week" else month_start(start)
    buckets = []
    cursor = first
    while cursor <= end:
        nxt = cursor + timedelta(days=7) if granularity == "week" else _next_month(cursor)
        members = [r for r in rows if cursor <= date.fromisoformat(r["date"]) < nxt]
        buckets.append(
            {"start": cursor.isoformat(), "end": (nxt - timedelta(days=1)).isoformat(), **cardio_totals(members)}
        )
        cursor = nxt
    return {
        **totals,
        # Share of the time spent in the gym (cardio minutes / duration of the completed sessions).
        "share": round(totals["minutes"] * 60 / duration, 4) if duration else None,
        "buckets": buckets,
        "sessions": [
            {"session_id": s["id"], "date": s["date"], "minutes": s["cardio_minutes"]}
            for s in sessions
            if s.get("cardio_minutes")
        ],
    }


CSV_HEADER = [
    "Datum", "Start", "Ende", "Status", "Plan", "Übung", "Gruppe", "Satz",
    "Wiederholungen", "Sekunden", "Gewicht (kg)", "RPE", "Übersprungen", "Notiz", "Archiviert",
    "Cardio-Phase", "Gerät", "Minuten",
]  # fmt: skip
STATUS_LABELS = {"running": "laufend", "completed": "abgeschlossen", "cancelled": "abgebrochen"}


def _csv_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}".replace(".", ",")


def _csv_order(row: dict[str, Any]) -> tuple[Any, ...]:
    """Per session: cardio warm-up, then the exercises, then the cool-down."""
    phase = {"warmup": 0, None: 1, "cooldown": 2}[row.get("phase")]
    return (row["started_at"], row.get("session_id", 0), phase)


def build_csv(rows: list[dict[str, Any]], cardio_rows: list[dict[str, Any]] | None = None) -> str:
    """CSV for German Excel: semicolon separated, decimal comma, UTF-8 BOM.

    Strength exercises have one row per set; cardio entries have own rows with phase, device and minutes.
    """
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in sorted([*rows, *(cardio_rows or [])], key=_csv_order):
        if row.get("phase"):
            status = STATUS_LABELS.get(row["status"], row["status"])
            writer.writerow(
                [
                    row["date"], row["started_at"], row["ended_at"] or "", status, row["plan"], "Cardio",
                    "", "", "", "", "", "", "", "", "",
                    CARDIO_PHASE_LABELS[row["phase"]], CARDIO_DEVICES[row["device"]], row["minutes"],
                ]
            )  # fmt: skip
            continue
        writer.writerow(
            [
                row["date"],
                row["started_at"],
                row["ended_at"] or "",
                STATUS_LABELS.get(row["status"], row["status"]),
                row["plan"],
                row["exercise"],
                row["muscle_group"],
                row["set_no"] if row["set_no"] is not None else "",
                row["reps"] if row["reps"] is not None else "",
                row["seconds"] if row["seconds"] is not None else "",
                _csv_number(row["weight"]),
                row["rpe"] if row["rpe"] is not None else "",
                "ja" if row["skipped"] else "nein",
                row["note"] or "",
                "ja" if row.get("archived") else "nein",
                "",
                "",
                "",
            ]
        )
    return out.getvalue()
